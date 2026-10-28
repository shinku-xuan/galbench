"""Documented Claude Code and Kimi Code non-interactive transports.

These functions only build invocations and decode already-successful processes.
The caller must reject nonzero exit codes before calling ``parse``. Kimi Print
does not emit a terminal success event: its clean process exit is authoritative.
Sources: code.claude.com/docs/en/{cli-reference,headless}; official MoonshotAI
kimi-code prompt-render.ts and kimi-cli ui/print/{__init__,visualize}.py.
"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import subprocess
from pathlib import Path

from ..api import APIError

SPECS = {
    "claude_code": {
        "label": "Claude Code", "command": "claude", "login": "claude auth login",
        "docs": "https://code.claude.com/docs/en/cli-reference",
        "model_override": True, "image_input": True,
        "notes": "使用支持 --safe-mode 的 CLI，保留订阅登录；禁用工具、MCP、持久会话。图片通过 JSON stdin 原生传入，不使用 Read 工具。",
    },
    "kimi_code": {
        "label": "Kimi Code", "command": "kimi", "login": "kimi login",
        "docs": "https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html",
        "model_override": True, "image_input": True, "acp_images": True,
        "acp_tool_policy": "requested_and_audited_only",
        "notes": "文本自动识别新旧 Print 参数并使用空工具 agent。新版图片走 ACP 原生图像，ACP 请求不使用工具并严格审计，不能宣称已硬禁工具；图片模型须先在客户端选择，模型覆盖留空。成功以进程正常退出或 ACP 完成响应为准，用量可能未知。",
    },
}


def _require(capabilities, *flags):
    missing = sorted(set(flags) - set(capabilities))
    if missing:
        raise ValueError("当前 Harness CLI 缺少必要参数：" + ", ".join(missing) + "；请升级官方 CLI 或选择其他 Harness")


def invocation(provider, config, root, prompt, images, capabilities):
    root = Path(root)
    if provider == "claude_code":
        _require(capabilities, "--print", "--input-format", "--output-format", "--verbose",
                 "--tools", "--disallowedTools", "--strict-mcp-config", "--mcp-config",
                 "--no-session-persistence", "--safe-mode")
        mcp = root / "galbench-empty-mcp.json"
        mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
        args = ["--print", "--safe-mode", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose", "--tools", "",
                "--disallowedTools", "*", "--strict-mcp-config", "--mcp-config", str(mcp),
                "--no-session-persistence"]
        if config.cli_model:
            _require(capabilities, "--model")
            args += ["--model", config.cli_model]
        if "--permission-prompts" in capabilities:
            args += ["--permission-prompts", "none"]
        content = [{"type":"text", "text":prompt}]
        for path in images:
            path = Path(path)
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                raise ValueError("Claude Code 仅接受 PNG / JPEG / GIF / WebP 原生图片")
            content.append({"type":"image", "source":{"type":"base64", "media_type":mime,
                "data":base64.b64encode(path.read_bytes()).decode("ascii")}})
        stdin = json.dumps({"type":"user", "message":{"role":"user", "content":content}}, ensure_ascii=False) + "\n"
        return args, stdin, {}
    if provider != "kimi_code":
        raise ValueError("Unsupported Harness")
    if images:
        raise ValueError("Kimi Code Print 不支持可核验的原生图片输入；请改用支持图片的 Harness，不能用 ReadMediaFile 工具代替识图输入")
    _require(capabilities, "--output-format", "--agent-file", "--skills-dir")
    skills = root / "galbench-empty-skills"
    skills.mkdir(exist_ok=True)
    if "--print" in capabilities:
        # Legacy Python print mode. Its JSON user reader extracts only text.
        _require(capabilities, "--mcp-config-file")
        system = root / "galbench-system.md"
        system.write_text("你正在参加独立的 galbench 闭卷测试。仅根据题面作答，不调用工具、不访问文件或网络，只输出要求的最终答案。", encoding="utf-8")
        agent = root / "galbench-agent.yaml"
        agent.write_text("version: 1\nagent:\n  name: galbench\n  system_prompt_path: ./galbench-system.md\n  tools: []\n  subagents: {}\n", encoding="utf-8")
        mcp = root / "galbench-empty-mcp.json"
        mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
        args = ["--print", "--output-format", "stream-json", "--agent-file", str(agent),
                "--skills-dir", str(skills), "--mcp-config-file", str(mcp)]
        if "--work-dir" in capabilities:
            args += ["--work-dir", str(root)]
        stdin = prompt
    else:
        _require(capabilities, "--prompt")
        # New native Kimi Code uses Markdown profiles, not legacy YAML specs.
        agent = root / "galbench-agent.md"
        agent.write_text("---\nname: galbench\ndescription: Independent closed-book galbench evaluation\ntools: []\nsubagents: []\n---\n你正在参加独立的 galbench 闭卷测试。仅根据题面作答，不使用工具，不访问文件或网络，只输出要求的最终答案。\n", encoding="utf-8")
        args = ["--prompt", prompt, "--output-format", "stream-json", "--agent-file", str(agent),
                "--skills-dir", str(skills)]
        stdin = ""
    if config.cli_model:
        _require(capabilities, "--model")
        args += ["--model", config.cli_model]
    if "--print" not in capabilities and os.name == "nt" and len(subprocess.list2cmdline(["kimi", *args]).encode("utf-16-le")) // 2 > 30000:
        raise ValueError("Kimi Code --prompt 超过 Windows 命令行长度上限；请缩短片段或使用支持 stdin 的 Harness")
    return args, stdin, {}


def acp_invocation(provider, config, root, capabilities):
    """New Kimi's ACP entry point; interactive JSON-RPC is owned by the caller.

    The official acp subcommand DOES NOT forward --agent-file / --skills-dir.
    Do not add them or claim the Print tool profile applies here. The caller
    must immediately reject tool notifications and reverse permission/file/
    terminal RPCs, and record this weaker policy in each response's metadata.
    """
    if provider != "kimi_code":
        raise ValueError("ACP images are only implemented for Kimi Code")
    if "--print" in capabilities:
        raise ValueError("旧 Python Kimi ACP 无法沿用空工具 agent；原生识图请升级新版 Kimi Code CLI")
    _require(capabilities, "--prompt", "--agent-file", "--skills-dir")
    if config.cli_model:
        raise ValueError("Kimi Code ACP 图片暂不支持本次模型覆盖；请先在客户端选定模型，再将模型调用 ID 留空")
    return ["acp"], {"KIMI_DISABLE_TELEMETRY":"1", "KIMI_CODE_BACKGROUND_KEEP_ALIVE_ON_EXIT":"0"}


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(v["text"] for v in value if isinstance(v,dict)
                       and v.get("type")=="text" and isinstance(v.get("text"),str))
    return ""


def _response(provider, events, text, *, model=None, model_provider=None, usage=None, finish="stop", warnings=None, refusal=None):
    if not isinstance(text, str) or not text.strip():
        raise APIError("agent_missing_final_answer：Harness 没有返回最终答案", code="agent_missing_final_answer")
    from ..clients import normalize_usage
    return {"text":text, "refusal":refusal, "finish_reason":finish,
            "returned_model":model if isinstance(model,str) and model.strip() else None,
            "returned_provider":model_provider if isinstance(model_provider,str) and model_provider.strip() else None,
            "usage":normalize_usage(usage), "warnings":warnings or [],
            "raw_response":{"provider":provider, "events":events}}


def parse(provider, events):
    from ..clients import execution_error, redact_diagnostic, tool_event
    if not events or any(not isinstance(e,dict) for e in events):
        raise APIError("agent_invalid_output：Harness 未返回约定的 JSON 事件", code="agent_invalid_output")
    if any(tool_event(e) or e.get("role")=="tool" or e.get("permission_denials") for e in events):
        raise APIError("agent_tool_use_detected：检测到工具请求，本题不纳入知识评分", code="agent_tool_use_detected")
    if provider == "claude_code":
        results = [e for e in events if e.get("type")=="result"]
        if len(results) != 1:
            raise APIError("agent_incomplete：Claude Code 缺少唯一的 result 完成事件", code="agent_incomplete")
        result = results[0]
        if result.get("subtype")!="success" or result.get("is_error") is not False or result.get("terminal_reason") not in {None,"completed"}:
            error = execution_error(0, "", json.dumps(result,ensure_ascii=False))
            if isinstance(result.get("result"),str):
                if not isinstance(error.details,dict): error.details={}
                error.details["available_answer"]=result["result"]
            raise error
        assistants = [e.get("message") for e in events if e.get("type")=="assistant"
                      and isinstance(e.get("message"),dict) and not e.get("parent_tool_use_id")]
        final = assistants[-1] if assistants else {}
        model = final.get("model")
        model_usage = result.get("modelUsage")
        model_usage = model_usage if isinstance(model_usage,dict) else {}
        if not model and len(model_usage)==1:
            model = next(iter(model_usage))
        selected = model_usage.get(model, {}) if isinstance(model,str) else {}
        selected = selected if isinstance(selected,dict) else {}
        usage = result.get("usage")
        if model_usage:
            usage = {}
            for field in ("inputTokens","outputTokens"):
                values = [v.get(field) for v in model_usage.values() if isinstance(v,dict)]
                if values and all(type(v) in (int,float) and math.isfinite(v) and v >= 0 for v in values):
                    usage[field]=sum(values)
        stop = result.get("stop_reason") or final.get("stop_reason")
        if stop not in {None,"end_turn","stop_sequence","max_tokens","max_output_tokens","length","refusal"}:
            raise APIError("agent_incomplete：Claude Code 尚未结束最终回答", code="agent_incomplete")
        text = result.get("result")
        return _response(provider,events,text,model=model,model_provider=selected.get("provider"),usage=usage,
            finish="length" if stop in {"max_tokens","max_output_tokens","length"} else "stop",
            refusal=text if stop=="refusal" else None)
    if provider != "kimi_code":
        raise ValueError("Unsupported Harness")
    errors = [e for e in events if e.get("type")=="error" or e.get("is_error")]
    if errors:
        raise execution_error(0,"",json.dumps(errors[-1],ensure_ascii=False))
    final = next((e for e in reversed(events) if e.get("role")=="assistant" and _text(e.get("content")).strip()),None)
    if final is None:
        raise APIError("agent_incomplete：Kimi Code 没有最终 assistant 正文", code="agent_incomplete")
    warnings=[]
    for e in events:
        if e.get("role")=="meta" and e.get("type")=="turn.step.retrying":
            warnings.append(redact_diagnostic(str(e.get("error_message") or "Kimi Code recovered a provider retry")))
    # Unlike Claude, Kimi Print has no result/model/usage frame. The transport's
    # zero exit status plus final assistant is the documented completion proof.
    return _response(provider,events,_text(final.get("content")),usage={},warnings=warnings)
