"""A single fresh ACP v1 conversation over bounded, cancellable stdio."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import signal
import subprocess
import threading
import time

from .api import APIError
from .control import CURRENT_CANCEL


IMAGE_CAPABILITY_WARNING = "agent_image_capability_unadvertised：ACP 未声明图片能力；按测试者的识图确认继续，是否可用以实际请求结果为准。"


def image_capability_warnings(capabilities, expects_images):
    return [IMAGE_CAPABILITY_WARNING] if expects_images and not capabilities.get("promptCapabilities",{}).get("image") else []


def complete_acp(command, config, root, prompt, images, environment=None, *, probe=False, require_images=False):
    # Import lazily: clients owns shared launcher and process-tree primitives.
    from .clients import (MAX_CAPTURE, child_environment, execution_error, normalize_usage,
                          redact_diagnostic, resume_windows_process, stop_process, windows_job)
    from . import __version__

    cancel = CURRENT_CANCEL.get()
    if cancel: cancel.check()
    kwargs = {"creationflags":subprocess.CREATE_NO_WINDOW | 4} if os.name=="nt" else {"start_new_session":True}
    try:
        process = subprocess.Popen(command,cwd=root,env={**child_environment(config.credential_env),**(environment or {})},
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,**kwargs)
    except OSError:
        raise APIError("agent_acp_launch_failed：无法启动 ACP，请检查官方 CLI 和本地权限", code="agent_acp_launch_failed", fatal=True) from None
    process._galbench_job = windows_job(process)
    if os.name=="nt":
        try: resume_windows_process(process)
        except Exception:
            stop_process(process); process.wait(timeout=10)
            if process._galbench_job:
                kernel,handle=process._galbench_job;kernel.CloseHandle(handle);process._galbench_job=None
            for pipe in (process.stdin,process.stdout,process.stderr):pipe.close()
            raise

    incoming=queue.Queue(); stderr=bytearray(); exceeded=threading.Event(); timed_out=threading.Event(); done=threading.Event()
    def stdout_reader():
        size=0
        try:
            while line:=process.stdout.readline(MAX_CAPTURE+1):
                size+=len(line)
                if size>MAX_CAPTURE: exceeded.set();stop_process(process);break
                incoming.put(line)
        finally:
            incoming.put(None);process.stdout.close()
    def stderr_reader():
        try:
            while chunk:=process.stderr.read1(65536):
                if len(stderr)+len(chunk)>MAX_CAPTURE:exceeded.set();stop_process(process);break
                stderr.extend(chunk)
        finally:process.stderr.close()
    def watchdog():
        if not done.wait(config.timeout_seconds):timed_out.set();stop_process(process)
    readers=[threading.Thread(target=stdout_reader,daemon=True),threading.Thread(target=stderr_reader,daemon=True)]
    for reader in readers:reader.start()
    guard=threading.Thread(target=watchdog,daemon=True);guard.start()
    remove=cancel.register(lambda:stop_process(process)) if cancel else None
    session=None;chunks=[];events=[];usage={};identity=None;finish=None;refusal=None;close_error=False;closing_at=None
    phase="initialize";capabilities={};session_options=[]

    def send(value):
        try:process.stdin.write((json.dumps(value,ensure_ascii=False)+"\n").encode("utf-8"));process.stdin.flush()
        except (BrokenPipeError,OSError):
            if cancel:cancel.check()
            if timed_out.is_set():raise APIError("agent_timeout：ACP 超时，已终止进程树",code="agent_request_timeout") from None
            raise APIError("agent_acp_disconnected：ACP 在请求完成前断开",code="agent_acp_disconnected") from None
    def request(identifier,method,params):send({"jsonrpc":"2.0","id":identifier,"method":method,"params":params})
    def fail_tool(event):
        # No filesystem/terminal/permission callbacks are ever executed.
        if "id" in event:
            if event.get("method")=="session/request_permission":
                send({"jsonrpc":"2.0","id":event["id"],"result":{"outcome":{"outcome":"cancelled"}}})
            else:send({"jsonrpc":"2.0","id":event["id"],"error":{"code":-32601,"message":"Tools disabled for galbench"}})
        raise APIError("agent_tool_use_detected：ACP 请求了工具或权限，本题不纳入知识评分",code="agent_tool_use_detected")

    try:
        request(1,"initialize",{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":False,"writeTextFile":False},"terminal":False},
            "clientInfo":{"name":"galbench","version":__version__}})
        expected=1
        while finish is None or expected==4:
            if cancel:cancel.check()
            if expected==4 and closing_at is not None and time.monotonic()-closing_at>1:
                close_error=True;break  # Unsupported cleanup must not stall a completed answer.
            if exceeded.is_set():raise APIError("agent_output_too_large：ACP 日志超过 16 MiB",code="agent_output_too_large")
            if timed_out.is_set():raise APIError("agent_timeout：ACP 超时，已终止本次进程树",code="agent_request_timeout")
            try:line=incoming.get(timeout=.1)
            except queue.Empty:continue
            if line is None:
                if cancel:cancel.check()
                if timed_out.is_set():raise APIError("agent_timeout：ACP 超时，已终止本次进程树",code="agent_request_timeout")
                if exceeded.is_set():raise APIError("agent_output_too_large：ACP 日志超过 16 MiB")
                if finish is not None:break
                process.wait(timeout=5)
                if process.returncode:raise execution_error(process.returncode,bytes(stderr).decode("utf-8",errors="replace"))
                raise APIError("agent_incomplete：ACP 在最终 prompt 响应前退出",code="agent_incomplete")
            try:event=json.loads(line.decode("utf-8-sig"))
            except (ValueError,UnicodeError):
                raise APIError("agent_invalid_output：ACP stdout 混入非 JSON-RPC 内容（"+phase+"），请查看错误详情",code="agent_invalid_output",
                    fatal=phase!="session/prompt",details={"stdout_preview":redact_diagnostic(line.decode("utf-8",errors="replace"))}) from None
            if not isinstance(event,dict) or event.get("jsonrpc")!="2.0":raise APIError("agent_invalid_output：ACP 消息缺少 JSON-RPC 版本")
            if "method" in event:
                if "id" in event:fail_tool(event)
                if event["method"]!="session/update":continue
                params=event.get("params",{})
                if params.get("sessionId")!=session:raise APIError("agent_session_mismatch：ACP 返回了其他会话的数据")
                update=params.get("update",{});kind=update.get("sessionUpdate")
                if kind in {"tool_call","tool_call_update"}:fail_tool(event)
                if kind=="agent_message_chunk":
                    content=update.get("content",{})
                    if content.get("type")=="text" and isinstance(content.get("text"),str):chunks.append(content["text"])
                    events.append({"type":"agent_message_chunk","content":content})
                # ACP context size/used is NOT per-call input/output usage.
                if kind=="usage_update" and isinstance(update.get("usage"),dict):usage=normalize_usage(update["usage"])
                continue
            if event.get("id")!=expected:raise APIError("agent_acp_unexpected_response：ACP 响应 ID 不匹配")
            if expected==4:
                close_error="error" in event
                expected=None;break
            if "error" in event:
                error=execution_error(1,"",json.dumps(event,ensure_ascii=False))
                if chunks:
                    error.details=error.details or {};error.details["available_answer"]="".join(chunks)
                raise error
            result=event.get("result")
            if not isinstance(result,dict):raise APIError("agent_invalid_output：ACP 缺少结果对象")
            if expected==1:
                if result.get("protocolVersion")!=1:raise APIError("agent_acp_version：此适配器要求 ACP v1",fatal=True)
                capabilities=result.get("agentCapabilities",{})
                # This is a declaration, not proof of the selected model's
                # actual capability. The user's supports_vision confirmation
                # controls whether images are attempted; never discard them.
                phase="session/new"
                request(2,"session/new",{"cwd":str(root.resolve()),"mcpServers":[]});expected=2
            elif expected==2:
                session=result.get("sessionId")
                if not isinstance(session,str) or not session:raise APIError("agent_invalid_output：ACP 没有创建新会话")
                model_state=result.get("models",{})
                identity=model_state.get("currentModelId") if isinstance(model_state,dict) else None
                # Kimi advertises actual routing IDs here; DSH option values are opaque.
                if config.provider=="kimi_code" and not identity:
                    identity=next((o.get("currentValue") for o in result.get("configOptions",[]) if o.get("id")=="model"),None)
                session_options=[{k:o[k] for k in ("id","currentValue") if k in o} for o in result.get("configOptions",[]) if isinstance(o,dict) and o.get("category")=="model"]
                if config.provider=="deepseek_harness":
                    # A functioning ACP bridge alone does not prove our tool
                    # policy mounted. Never issue a prompt without attestation.
                    deadline=time.monotonic()+1
                    while b"GALBENCH_SESSION_POLICY_READY" not in stderr and time.monotonic()<deadline:
                        if cancel:cancel.check()
                        if timed_out.is_set():break
                        done.wait(.01)
                    if b"GALBENCH_POLICY_READY" not in stderr or b"GALBENCH_SESSION_POLICY_READY" not in stderr or b"entries did not activate" in stderr:
                        raise APIError("agent_policy_not_active：DeepSeek ACP 的评测插件或必要配置未正常启用，未发送题目；请查看错误详情",code="agent_policy_not_active",fatal=True)
                if probe:
                    finish="probe";phase="session/close"
                    request(4,"session/close",{"sessionId":session});expected=4;closing_at=time.monotonic()
                    continue
                parts=[{"type":"text","text":prompt}]
                for path in images:
                    mime=mimetypes.guess_type(str(path))[0]
                    if mime not in {"image/png","image/jpeg","image/webp","image/gif"}:raise APIError("agent_image_unsupported：不支持的图片格式")
                    parts.append({"type":"image","mimeType":mime,"data":base64.b64encode(path.read_bytes()).decode("ascii")})
                phase="session/prompt"
                request(3,"session/prompt",{"sessionId":session,"prompt":parts});expected=3
            elif expected==3:
                reason=result.get("stopReason")
                if reason=="end_turn":finish="stop"
                elif reason=="max_tokens":finish="length"
                elif reason=="refusal":finish="stop";refusal="模型拒绝回答"
                else:raise APIError("agent_incomplete：ACP 未正常结束回答",code="agent_incomplete",details={"stop_reason":reason,"available_answer":"".join(chunks)})
                if isinstance(result.get("usage"),dict):usage=normalize_usage(result["usage"])
                if not "".join(chunks).strip() and not refusal:raise APIError("agent_missing_final_answer：ACP 没有最终正文")
                phase="session/close"
                request(4,"session/close",{"sessionId":session});expected=4;closing_at=time.monotonic()
        if probe:
            return {"protocol":"acp-v1","agent_capabilities":capabilities,"model_options":session_options,
                    "model_request_sent":False,"tool_policy_verified":config.provider=="deepseek_harness",
                    "warnings":image_capability_warnings(capabilities,require_images)}
        text="".join(chunks)
        model=identity if isinstance(identity,str) and identity.strip() else None
        return {"text":text,"refusal":refusal,"finish_reason":finish,"returned_model":model,
            "returned_models":[model] if model else [],"returned_provider":None,"usage":usage,
            "warnings":image_capability_warnings(capabilities,bool(images)),
            "completion_evidence":"protocol_end_turn_only" if config.provider=="kimi_code" else "correlated_prompt_response",
            "raw_response":{"provider":config.provider,"protocol":"acp-v1","events":events,"session_closed":not close_error,
                "agent_capabilities":capabilities,"image_input_attempted":bool(images)}}
    except APIError as exc:
        if not exc.code:
            prefix=str(exc).partition("：")[0]
            exc.code=prefix if prefix.startswith("agent_") else "agent_invalid_output"
        exc.details={**(exc.details or {}),"acp_phase":phase}
        if images and phase=="session/prompt":
            # A rejected image belongs to this question. Keep the evidence and
            # let the tester decide whether to stop the remaining evaluation.
            exc.fatal=False
            exc.details.update(image_input_attempted=True,
                image_capability_advertised=bool(capabilities.get("promptCapabilities",{}).get("image")))
        if config.provider=="deepseek_harness" and config.cli_model:exc.details["configured_route"]=config.cli_model
        diagnostic=redact_diagnostic(bytes(stderr)[-6000:].decode("utf-8",errors="replace"))
        if diagnostic:exc.details["stderr_preview"]=diagnostic
        if chunks:exc.details.setdefault("available_answer","".join(chunks))
        raise
    finally:
        done.set()
        if remove:remove()
        try:process.stdin.close()
        except OSError:pass
        if process.poll() is None:
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:stop_process(process)
        if process.poll() is None:process.wait(timeout=10)
        if process._galbench_job:
            kernel,handle=process._galbench_job;kernel.CloseHandle(handle);process._galbench_job=None
        elif os.name!="nt":
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        for reader in readers:reader.join(timeout=2)
        guard.join(timeout=1)
