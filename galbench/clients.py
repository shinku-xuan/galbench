"""Model transports. Official CLI clients own their login and subscription state."""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .api import APIError, ChatClient, ModelConfig
from .control import CURRENT_CANCEL
from .harnesses import SPECS, adapter
from .discovery import extra_cli_candidates

AGENTS = {
    "codex": {"label": "Codex", "command": "codex", "login": "codex login",
              "docs": "https://learn.chatgpt.com/docs/non-interactive-mode"},
    "minimax_code": {"label": "MiniMax Code", "command": "mcode", "login": "mcode login",
                     "docs": "https://agent.minimax.io/docs/cli/quick-start"},
    "zcode": {"label": "ZCode", "command": "zcode", "login": "zcode login",
              "docs": "https://github.com/zai-org/ZCode/tree/main/apps/zcode-cli/docs/usage"},
}
AGENTS.update(SPECS)
for _provider, _info in AGENTS.items():
    _info.setdefault("model_override", _provider != "zcode")
    _info.setdefault("image_input", True)
    _info.setdefault("notes", "每题使用新会话，沿用客户端认证与生成预算，检测到工具调用的答卷不纳入评分。")
ADAPTER_VERSION = "official-cli-5"
MAX_CAPTURE = 16 * 1024 * 1024
CODEX_MODELS = [
    {"id": "gpt-6-luna", "label": "GPT-6 Luna"},
    {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
    {"id": "gpt-6-sol", "label": "GPT-6 Sol"},
    {"id": "gpt-6-astra", "label": "GPT-6 Astra"},
]


def normalize_codex_model(value):
    key = re.sub(r"[\s_-]+", "", value).casefold()
    for model in CODEX_MODELS:
        if key == re.sub(r"[\s_-]+", "", model["id"]).casefold():
            return model["id"]
    value = value.strip()
    if any(c.isspace() for c in value):
        raise ValueError("Codex 模型请填写调用 ID，例如 gpt-6-luna；未知显示名称不能直接传给 CLI")
    return value


@dataclass(frozen=True)
class AgentConfig:
    provider: str
    executable: str = ""
    cli_model: str = ""
    supports_vision: bool = False
    timeout_seconds: float = 600
    use_client_config: bool = False
    cli_profile: str = ""
    model_label: str = ""
    credential_env: str = ""

    def __post_init__(self):
        if self.provider not in AGENTS:
            raise ValueError("Unsupported agent provider")
        for value in (self.executable, self.cli_model, self.model_label, self.cli_profile, self.credential_env):
            if not isinstance(value, str) or any(ord(c) < 32 for c in value) or len(value) > 4096:
                raise ValueError("CLI path/model must be plain text without control characters")
        if self.cli_model.lstrip().startswith("-"):
            raise ValueError("CLI model cannot start with '-' ")
        if self.provider == "codex":
            object.__setattr__(self, "cli_model", normalize_codex_model(self.cli_model))
        if not AGENTS[self.provider]["model_override"] and self.cli_model:
            raise ValueError(f"{AGENTS[self.provider]['label']} 不支持 --model；请先在客户端选择模型，模型覆盖留空")
        if type(self.supports_vision) is not bool:
            raise ValueError("supports_vision must be a boolean")
        if type(self.use_client_config) is not bool:
            raise ValueError("use_client_config must be a boolean")
        if self.cli_profile and (self.provider != "codex" or not self.use_client_config
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.cli_profile)):
            raise ValueError("Codex profile 须为字母、数字、下划线或连字符，并启用客户端配置")
        if len(self.model_label) > 160:
            raise ValueError("参赛模型名称不能超过 160 字")
        names = self.credential_env.split(",") if self.credential_env else []
        if len(names) > 16 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", n.strip()) for n in names):
            raise ValueError("凭据环境变量只填写变量名，用英文逗号分隔；不要填写密钥值")
        if (isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 1800):
            raise ValueError("CLI timeout must be in [1,1800] seconds")

    @property
    def name(self):
        return self.model_label or self.cli_model or self.provider + "/client-default"

    def public_dict(self):
        return {"provider": self.provider, "name": self.name, "executable": self.executable,
                "cli_model": self.cli_model, "supports_vision": self.supports_vision,
                "timeout_seconds": self.timeout_seconds, "adapter_version": ADAPTER_VERSION,
                "use_client_config": self.use_client_config, "cli_profile": self.cli_profile,
                "model_label": self.model_label, "credential_env": self.credential_env,
                "auth": "official_client", "tool_policy": "no_tools_requested_and_audited",
                "isolation": "fresh_workspace_and_session; not_an_OS_security_boundary"}


def model_config(value):
    if not isinstance(value, dict):
        raise ValueError("Model configuration must be a table")
    values = dict(value)
    provider = values.pop("provider", None)
    if provider not in AGENTS:
        raise ValueError("直接 API 配置已移除，请填写受支持的 Agent provider")
    return AgentConfig(provider=provider, **values)


def create_client(config, *, api_key=None):
    if isinstance(config, AgentConfig):
        if api_key:
            raise ValueError("Agent 通道使用官方客户端登录，不接收 API Key")
        return AgentClient(config)
    if isinstance(config, ModelConfig):  # Restricted to loopback at construction; not loadable from model configs.
        return ChatClient(config, api_key=api_key)
    raise ValueError("模型必须通过 Agent Harness 接入")


def find_executable(config):
    requested = config.executable.strip() or AGENTS[config.provider]["command"]
    found = shutil.which(requested)
    if not found and Path(requested).is_file():
        found = str(Path(requested).resolve())
    if not found and not config.executable and config.provider == "codex" and os.name == "nt":
        # Desktop launches do not inherit the PATH injected into a Codex agent.
        profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        local_roots = list(dict.fromkeys([Path(os.environ.get("LOCALAPPDATA", str(profile / "AppData" / "Local"))),
                                         profile / "AppData" / "Local"]))
        app_roots = list(dict.fromkeys([Path(os.environ.get("APPDATA", str(profile / "AppData" / "Roaming"))),
                                       profile / "AppData" / "Roaming"]))
        installed = []
        if os.environ.get("CODEX_INSTALL_DIR"):
            installed += [Path(os.environ["CODEX_INSTALL_DIR"]) / "codex.exe",
                          Path(os.environ["CODEX_INSTALL_DIR"]) / "codex.cmd"]
        for local in local_roots:
            installed += [local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe",
                          local / "Microsoft" / "WinGet" / "Links" / "codex.exe"]
        installed += [app_data / "npm" / "codex.cmd" for app_data in app_roots]
        for local in local_roots:
            desktop = local / "OpenAI" / "Codex" / "bin"
            installed += [desktop / "codex.exe"]
            if desktop.is_dir():
                installed += sorted((p for p in desktop.glob("*/codex.exe") if p.is_file()),
                                    key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
        found = next((str(p) for p in installed if p.is_file()), None)
    if not found and not config.executable and config.provider == "minimax_code":
        install = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".minimax-code"
        for launcher in (install / "mcode.cmd", install / "bin" / "mcode.cmd"):
            if launcher.is_file(): found = str(launcher); break
    if not found and not config.executable and os.name == "nt":
        profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        command = AGENTS[config.provider]["command"]
        candidates = [profile/".local"/"bin"/(command+".exe"), profile/".local"/"bin"/(command+".cmd"),
            profile/".bun"/"bin"/(command+".exe"), profile/".cargo"/"bin"/(command+".exe"),
            Path(os.environ.get("APPDATA", str(profile/"AppData"/"Roaming")))/"npm"/(command+".cmd")]
        if config.provider=="workbuddy":
            native=Path(os.environ.get("LOCALAPPDATA",str(profile/"AppData"/"Local")))/"codebuddy"/"bin"
            candidates += [native/"codebuddy.exe",native/"codebuddy.cmd"]
        found = next((str(p) for p in candidates if p.is_file()),None)
    if not found:
        if not config.executable and os.name == "nt":
            found = next((str(path) for path in extra_cli_candidates(config.provider) if path.is_file()),None)
    if not found:
        raise ValueError(f"未找到 {AGENTS[config.provider]['command']}；请安装官方 CLI，或填写可执行文件的完整路径")
    return Path(found).resolve()


class LaunchCommand(list):
    """Argument vector carrying the private environment required by its runtime."""
    def __init__(self, args, environment):
        super().__init__(args)
        self.environment = environment


def electron_node_command(path, script):
    # Recognize the official immutable desktop launcher, never run its shell.
    lines = "\n".join(line.strip() for line in script.splitlines() if line.strip())
    pattern = (r'@echo off\nsetlocal DisableDelayedExpansion\nset "ELECTRON_RUN_AS_NODE=1"\n'
        r'"%~dp0(?P<exe>[^"\r\n]+\.exe)" --expose-internals "%~dp0(?P<entry>[^"\r\n]+\.js)" %\*\n'
        r'exit /b %errorlevel%')
    match = re.fullmatch(pattern,lines,re.I)
    if not match: raise ValueError("无法验证 Electron CLI 启动器格式；不会退回桌面 GUI 启动")
    executable = (path.parent/match["exe"].replace("\\","/")).resolve()
    entry = (path.parent/match["entry"].replace("\\","/")).resolve()
    archive = next((parent for parent in entry.parents if parent.name.casefold()=="app.asar"),None)
    if (executable.name.casefold()!="deepseek harness.exe" or not executable.is_file() or not archive
            or not archive.is_file() or archive.parent!=executable.parent/"resources"
            or entry.relative_to(archive).as_posix()!="dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"):
        raise ValueError("DeepSeek 桌面 CLI 的运行时或 ASAR 入口无效，请修复官方安装")
    return LaunchCommand([str(executable),"--expose-internals",str(entry)],{"ELECTRON_RUN_AS_NODE":"1"})


def executable_command(path, *, depth=0):
    """Unwrap Node launchers so question text never passes through cmd.exe."""
    suffix = path.suffix.lower()
    if depth > 4: raise ValueError("CLI 启动器嵌套过深，请指定实际入口")
    if suffix in {".cmd", ".bat", ".ps1"}:
        if path.stat().st_size > 32768:
            raise ValueError("CLI 启动脚本过大，请直接填写官方 .exe 或 .js/.cjs 入口")
        script = path.read_text(encoding="utf-8-sig")
        if "electron_run_as_node" in script.casefold(): return electron_node_command(path,script)
        # The current official Windows installer uses a stable release pointer,
        # followed by a launcher pinned to its compatible Node runtime.
        if path.name.lower() in {"mcode.cmd", "mcode.ps1"} and ".mcode-launcher" in script:
            pointer = path.parent / "current"
            if not pointer.is_file() or pointer.stat().st_size > 256:
                raise ValueError("MiniMax Code 安装缺少有效的 current 版本指针，请用官方安装器修复")
            release = pointer.read_text(encoding="utf-8-sig").strip()
            if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}", release):
                raise ValueError("MiniMax Code 版本指针无效")
            target = (path.parent / "releases" / release / ".mcode-launcher.cmd").resolve()
            if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
                raise ValueError("MiniMax Code 当前版本入口缺失，请用官方安装器修复")
            return executable_command(target, depth=depth+1)
        node_path = None
        pinned = re.search(r'''(?im)^\s*(?:&\s*)?["']([A-Za-z]:[\\/][^"'\r\n]*[\\/]node\.exe)["']''', script)
        if pinned and Path(pinned[1]).is_file(): node_path = pinned[1]
        if node_path is None and (path.parent / "node.exe").is_file(): node_path = str(path.parent / "node.exe")
        # Standard npm shims and the official installer's relative Node launcher.
        references = re.findall(r'(?:%~dp0|%dp0%|\$basedir|\$PSScriptRoot)[\\/]?([^"\r\n]*?\.(?:cjs|mjs|js|exe))', script, re.I)
        for reference in references:
            candidate = (path.parent / reference.replace("\\", "/")).resolve()
            if candidate.is_file() and candidate != path and candidate.name.lower() not in {"node.exe", "node"}:
                if node_path and candidate.suffix.lower() in {".js", ".cjs", ".mjs"}:
                    return [node_path, str(candidate)]
                return executable_command(candidate, depth=depth+1)
        raise ValueError("无法安全解析 CLI 启动器；请填写官方 .exe 或 .js/.cjs 入口，不能填写整条 shell 命令")
    if suffix in {".js", ".cjs", ".mjs"}:
        node = shutil.which("node")
        if not node:
            raise ValueError("此 CLI 入口需要 Node.js，请安装官方要求的 Node.js 或使用独立 .exe")
        return [node, str(path)]
    if os.name == "nt" and suffix != ".exe":
        raise ValueError("请选择官方 .exe、Node.js 入口或标准 npm 启动器")
    return [str(path)]


def child_environment(credential_env=""):
    # Keep login locations, networking and OS essentials. Forward additional
    # credential variables only when the user explicitly selects their names.
    allowed = {"path", "pathext", "systemroot", "windir", "comspec", "temp", "tmp", "tmpdir",
               "home", "userprofile", "appdata", "localappdata", "programfiles", "programfiles(x86)",
               "codex_home", "minimax_data_dir", "mavis_data_dir", "zcode_data_base_dir",
               "zcode_personal_provider_config_file", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
               "ssl_cert_file", "ssl_cert_dir", "node_extra_ca_certs", "zcode_http_proxy", "zcode_no_proxy",
               "xdg_config_home", "xdg_data_home", "xdg_state_home", "xdg_cache_home", "dsh_home", "kimi_home",
               "qoder_config_dir", "codebuddy_config_dir", "opencode_config_dir"}
    selected = {name.strip().casefold() for name in credential_env.split(",") if name.strip()}
    return {**{k: v for k, v in os.environ.items() if k.lower() in allowed | selected},
            "NO_COLOR": "1", "CI": "1"}


def windows_job(process):
    """Tie descendants to this invocation, including children of Node launchers."""
    if os.name != "nt": return None
    import ctypes
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IoCounters(ctypes.Structure):
        _fields_ = [(key, ctypes.c_uint64) for key in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle: return None
    limits = ExtendedLimits(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, int(process._handle))):
        kernel.CloseHandle(handle); return None
    return kernel, handle


def stop_process(process):
    if job := getattr(process, "_galbench_job", None):
        job[0].TerminateJobObject(job[1], 1)
        if process.poll() is None: process.kill()
        return
    if process.poll() is not None: return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
    else:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    if process.poll() is None: process.kill()


def resume_windows_process(process):
    """Resume only our newly suspended process after its job is attached.

    Launchers can create descendants before a post-launch job assignment. Starting
    suspended prevents that race, so stopping a CLI also closes its child pipes.
    """
    import ctypes
    from ctypes import wintypes
    class ThreadEntry(ctypes.Structure):
        _fields_=[("size",wintypes.DWORD),("usage",wintypes.DWORD),("thread_id",wintypes.DWORD),
                  ("process_id",wintypes.DWORD),("priority",wintypes.LONG),("delta",wintypes.LONG),("flags",wintypes.DWORD)]
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Thread32First.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenThread.restype=wintypes.HANDLE
    kernel.ResumeThread.argtypes=[wintypes.HANDLE];kernel.ResumeThread.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=kernel.CreateToolhelp32Snapshot(4,0)
    if snapshot==ctypes.c_void_p(-1).value: raise APIError("无法初始化本次 CLI 进程")
    try:
        entry=ThreadEntry();entry.size=ctypes.sizeof(entry);found=kernel.Thread32First(snapshot,ctypes.byref(entry))
        while found:
            if entry.process_id==process.pid:
                handle=kernel.OpenThread(2,False,entry.thread_id)
                if handle:
                    try:
                        if kernel.ResumeThread(handle)!=0xffffffff: return
                    finally: kernel.CloseHandle(handle)
            found=kernel.Thread32Next(snapshot,ctypes.byref(entry))
        raise APIError("无法启动本次 CLI 的执行线程")
    finally: kernel.CloseHandle(snapshot)


def execute(command, *, cwd, timeout, stdin="", credential_env="", environment=None):
    """Bound both output pipes, wall time and the child process tree."""
    cancel = CURRENT_CANCEL.get()
    if cancel: cancel.check()
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW|4} if os.name == "nt" else {"start_new_session": True}
    try:
        process = subprocess.Popen(command, cwd=cwd, env={**child_environment(credential_env), **(environment or {})}, shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except OSError:
        raise APIError("无法启动官方 CLI；请检查文件、运行时和本地执行权限") from None
    process._galbench_job = windows_job(process)
    if os.name=="nt":
        try: resume_windows_process(process)
        except Exception:
            stop_process(process);process.wait(timeout=10)
            for pipe in (process.stdin,process.stdout,process.stderr): pipe.close()
            if process._galbench_job:
                kernel,handle=process._galbench_job;process._galbench_job=None;kernel.CloseHandle(handle)
            raise
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    def read_pipe(pipe, dest):
        try:
            while chunk := pipe.read(65536):
                if len(dest) + len(chunk) > MAX_CAPTURE:
                    overflow.set(); stop_process(process); break
                dest.extend(chunk)
        finally: pipe.close()
    readers = [threading.Thread(target=read_pipe, args=(pipe, dest), daemon=True)
               for pipe, dest in zip((process.stdout, process.stderr), buffers)]
    for reader in readers: reader.start()
    def write_input():
        try: process.stdin.write(stdin.encode("utf-8")); process.stdin.flush()
        except (BrokenPipeError, OSError): pass
        finally:
            try: process.stdin.close()
            except OSError: pass
    writer = threading.Thread(target=write_input, daemon=True); writer.start()
    remove = None
    try:
        if cancel:
            remove = cancel.register(lambda:stop_process(process))
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                cancel.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise subprocess.TimeoutExpired(command,timeout)
                try: process.wait(timeout=min(.1,remaining))
                except subprocess.TimeoutExpired: pass
            cancel.check()
        else: process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process(process); process.wait(timeout=10)
        raise APIError("agent_timeout：官方 CLI 超时，已终止本次进程树；可提高超时后续跑") from None
    finally:
        if remove: remove()
        if process._galbench_job:
            kernel, handle = process._galbench_job
            process._galbench_job = None
            kernel.CloseHandle(handle)
        elif os.name != "nt":
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        for reader in readers: reader.join(timeout=3)
        writer.join(timeout=1)
    if overflow.is_set(): raise APIError("agent_output_too_large：CLI 日志超过 16 MiB，本次未判分")
    return process.returncode, *(bytes(b).decode("utf-8-sig", errors="replace") for b in buffers)


def redact_diagnostic(value):
    value = re.sub(r"(?i)\bBearer\s+[^\s\"',}]+", "Bearer [REDACTED]", value)
    value = re.sub(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)["']?\s*[:=]\s*)["']?[^\s"',}]+''', r"\1[REDACTED]", value)
    value = re.sub(r"\b(?:sk-|eyJ)[A-Za-z0-9_.-]{10,}", "[REDACTED]", value)
    value = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", value)
    return value[:1200]


def execution_error(exit_code, stderr, stdout=""):
    """Extract nested upstream errors, rather than treating arbitrary IDs as HTTP codes."""
    def unpack(node, depth=0):
        if depth > 8: return {}
        if isinstance(node, str):
            try: return unpack(json.loads(node), depth+1)
            except ValueError: return {"message": node}
        if not isinstance(node, dict): return {}
        info = {}
        if type(node.get("status")) is int: info["http_status"] = node["status"]
        if isinstance(node.get("code"), str): info["upstream_code"] = node["code"]
        for key in ("error", "message"):
            if node.get(key): info.update(unpack(node[key], depth+1))
        return info

    candidates = []
    try:
        whole = json.loads(stdout)
        values = whole if isinstance(whole, list) else [whole]
    except ValueError:
        values = []
        for line in stdout.splitlines():
            try: values.append(json.loads(line))
            except ValueError: pass
    for value in values:
        if isinstance(value, dict) and (value.get("type") in {"error", "turn.failed", "exec.result"} or value.get("error")):
            candidates.append(unpack(value))
    info = next((v for v in reversed(candidates) if v.get("message")), {})
    message = redact_diagnostic(info.get("message") or stderr.strip() or "官方 CLI 未提供错误详情")
    status = info.get("http_status")
    if status is None:
        match = re.search(r"(?i)\b(?:HTTP(?:/\d(?:\.\d)?)?|status(?:_code)?)[\s:=]+(4\d\d|5\d\d)\b", message)
        if match: status = int(match[1])
    lower = message.casefold()
    upstream = info.get("upstream_code", "")
    code, text, fatal = "agent_process_failed", "官方 CLI 执行失败，请检查下方错误详情", False
    if upstream in {"model_not_found", "unsupported_model"} or re.search(r"\bmodel\b.{0,200}(not supported|not found|does not exist|unavailable)", lower):
        code, text, fatal = "agent_model_unavailable", "模型 ID 无效或当前账号无权使用；请核对模型调用 ID", True
    elif status == 401 or any(s in lower for s in ("unauthorized", "not logged", "not authenticated", "login required", "请登录")):
        code, text, fatal = "agent_login_required", "请先在终端运行官方登录命令", True
    elif status == 403:
        code, text, fatal = "agent_access_denied", "当前账号或工作区没有调用权限", True
    elif status == 429 or any(s in lower for s in ("quota", "rate limit", "credits", "额度", "余额")):
        code, text = "agent_quota_or_rate_limit", "请检查订阅额度，降低并发或稍后续跑"
    elif any(s in lower for s in ("unknown option", "unrecognized", "unexpected argument")):
        code, text, fatal = "agent_cli_version_unsupported", "CLI 缺少要求的参数，请更新官方 CLI", True
    elif any(s in lower for s in ("access is denied", "permission denied", "os error 5", "拒绝访问")):
        code, text, fatal = "agent_local_permission_denied", "CLI 本地启动被权限限制，请检查本地执行环境", True
    elif status == 400:
        code, text, fatal = "agent_invalid_request", "模型请求参数有误", True
    elif "timed out" in lower or "timeout" in lower:
        code, text = "agent_request_timeout", "官方 CLI 的模型请求超时；仅提高本地进程超时不能保证恢复，请降低并发或稍后续跑"
    elif any(s in lower for s in ("stream disconnected", "tls handshake eof", "connection reset", "connection closed")):
        code, text = "agent_connection_error", "官方 CLI 的连接在回答完成前中断，请降低并发或稍后续跑"
    details = {"exit_code": exit_code, "http_status": status,
               "upstream_code": redact_diagnostic(upstream), "message": message}
    return APIError(f"{code}：{text}（exit {exit_code}）\n{message}", code=code, fatal=fatal, details=details)


def cli_error(code, diagnostic):
    return str(execution_error(code, diagnostic))


def discover_agents():
    rows = []
    for provider, info in AGENTS.items():
        try: path = str(find_executable(AgentConfig(provider))); installed = True
        except ValueError: path = ""; installed = False
        rows.append({"id": provider, **info, "installed": installed, "executable": path})
    return rows


def normalize_usage(value):
    if not isinstance(value, dict): return {}
    result = {}
    aliases = {"prompt_tokens": ("prompt_tokens", "input_tokens", "inputTokens"),
               "completion_tokens": ("completion_tokens", "output_tokens", "outputTokens"),
               "total_tokens": ("total_tokens", "totalTokens")}
    for target, names in aliases.items():
        for name in names:
            n = value.get(name)
            if type(n) in (int, float) and math.isfinite(n) and n >= 0:
                result[target] = n; break
    if "total_tokens" not in result and all(k in result for k in ("prompt_tokens", "completion_tokens")):
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
    n = value.get("reasoning_output_tokens", value.get("reasoningTokens"))
    if type(n) in (int, float) and math.isfinite(n) and n >= 0:
        result["completion_tokens_details"] = {"reasoning_tokens": n}
    return result


def tool_event(value):
    if isinstance(value, list): return any(tool_event(v) for v in value)
    if not isinstance(value, dict): return False
    kind = str(value.get("type", "")).lower()
    if kind in {"command_execution", "file_change", "web_search", "mcp_tool_call", "tool_call", "tool_use", "function_call"}:
        return True
    if kind.startswith(("tool.", "tool_", "tool-")): return True
    if any(value.get(k) for k in ("toolCall", "toolCalls", "tool_calls", "tool_use")): return True
    return any(tool_event(v) for v in value.values() if isinstance(v, (dict, list)))


def parse_cli_output(provider, stdout):
    try:return _parse_cli_output(provider, stdout)
    except APIError as exc:
        if not exc.code:
            prefix=str(exc).partition("：")[0]
            exc.code=prefix if prefix.startswith("agent_") else "agent_invalid_output"
        exc.details={**(exc.details or {}),"provider":provider,"transport_phase":"cli_parse"}
        raise


def _parse_cli_output(provider, stdout):
    try:
        whole = json.loads(stdout)
        events = whole if isinstance(whole, list) else [whole]
    except ValueError:
        try: events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        except ValueError:
            bad=next((line for line in stdout.splitlines() if line.strip() and not _json_line(line)), "")
            raise APIError("agent_invalid_output：CLI 未返回约定的 JSON/JSONL；请核对官方 CLI 版本",code="agent_invalid_output",
                details={"stdout_preview":redact_diagnostic(bad)}) from None
    if not events or any(not isinstance(e, dict) for e in events):
        raise APIError("agent_invalid_output：缺少 CLI 事件")
    if provider in SPECS:
        return adapter(provider).parse(provider,events)
    if any(tool_event(event) for event in events):
        raise APIError("agent_tool_use_detected：检测到工具调用，本题不纳入知识评分")
    error_indices = [i for i,e in enumerate(events) if e.get("type") in {"turn.failed", "error"}]
    completed_indices = [i for i,e in enumerate(events) if e.get("type") == "turn.completed"]
    recovered = (provider == "codex" and completed_indices and not any(e.get("type") == "turn.failed" for e in events)
                 and all(i < completed_indices[-1] for i in error_indices))
    if error_indices and not recovered:
        error = execution_error(0, "", stdout)
        final_items = [e["item"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
                       and e["item"].get("type") == "agent_message" and e["item"].get("phase") in {None,"final","final_answer"}
                       and e["item"].get("channel") in {None,"final"}]
        if final_items and isinstance(final_items[-1].get("text"), str): error.details["available_answer"] = final_items[-1]["text"]
        raise error
    usage, model, model_provider = {}, None, None
    if provider == "codex":
        completed = [e for e in events if e.get("type") == "turn.completed"]
        if not completed: raise APIError("agent_incomplete：Codex 未返回 turn.completed")
        messages = [e["item"] for e in events if e.get("type") == "item.completed"
                    and isinstance(e.get("item"), dict) and e["item"].get("type") == "agent_message"]
        # Commentary may also be an agent_message. Only the last final message is graded.
        final = [m for m in messages if m.get("phase") in {None, "final", "final_answer"}
                 and m.get("channel") in {None, "final"}]
        text = (final or [{}])[-1].get("text")
        usage = normalize_usage(completed[-1].get("usage"))
        model = completed[-1].get("model")
    elif provider == "minimax_code":
        completed = [e.get("result") for e in events if e.get("type") == "exec.completed"]
        completed += [e for e in events if e.get("type") == "exec.result"]
        result = completed[-1] if completed else {}
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            raise execution_error(0, "", json.dumps(result, ensure_ascii=False))
        text = result.get("output")
        if isinstance(text, dict): text = json.dumps(text, ensure_ascii=False)
        usage = normalize_usage(result.get("usage"))
        identity = result.get("model") or {}
        if isinstance(identity, dict):
            model = identity.get("modelId")
            model_provider = identity.get("providerId")
    else:
        completed = [e for e in events if e.get("type") == "result" or ("response" in e and "sessionId" in e)]
        result = completed[-1] if completed else {}
        projection = result.get("projection") or {}
        if (result.get("error") or result.get("is_error") or result.get("status") in {"failed", "cancelled", "timeout"}
                or isinstance(projection, dict) and projection.get("status") in {"failed", "cancelled", "timeout", "aborted"}):
            raise execution_error(0, "", json.dumps(result, ensure_ascii=False))
        text = result.get("response")
        usage = normalize_usage(result.get("usage"))
        model = result.get("modelId")
    if not isinstance(text, str) or not text.strip():
        raise APIError("agent_missing_final_answer：CLI 未返回完整的最终正文")
    warnings = [redact_diagnostic(str(events[i].get("message") or events[i].get("error") or "CLI recovered an error")) for i in error_indices]
    # CLI-reported IDs describe routing, not an attestation of server-side weights.
    model = model.strip() if isinstance(model, str) and model.strip() else None
    model_provider = model_provider if isinstance(model_provider, str) else None
    return {"text": text, "refusal": None, "finish_reason": "stop", "returned_model": model,
            "returned_provider": model_provider, "warnings":warnings,
            "usage": usage, "raw_response": {"provider": provider, "events": events}}


def _json_line(line):
    try:json.loads(line);return True
    except ValueError:return False


class AgentClient:
    def __init__(self, config):
        self.config = config
        self._probe_lock = threading.Lock()
        self._probe_result = None
        self._probe_error = None
        if any(name.strip() not in os.environ for name in config.credential_env.split(",") if name.strip()):
            raise ValueError("指定的凭据环境变量不存在；只填变量名，请先在启动本程序的环境中设置，再重启服务")
        self.path = find_executable(config)
        self.command = executable_command(self.path)
        self.launch_environment = dict(getattr(self.command,"environment",{}))
        code, out, err = execute([*self.command, "--version"], cwd=tempfile.gettempdir(), timeout=15,environment=self.launch_environment)
        if code: raise ValueError(cli_error(code, err + out))
        self.version = out.strip()[:160]
        self.capabilities = set()
        if config.provider in SPECS:
            help_commands = [["--help"], SPECS[config.provider].get("help_args",["--help"])]
            for help_args in {tuple(args) for args in help_commands}:
                code, help_out, help_err = execute([*self.command,*help_args],cwd=tempfile.gettempdir(),timeout=15,environment=self.launch_environment)
                if code: raise ValueError("无法读取 Harness 帮助参数，请确认 CLI 版本与安装入口")
                self.capabilities.update(re.findall(r"--[A-Za-z][A-Za-z0-9-]*",help_out+help_err))
            # Build only. This validates documented flags without a model request.
            with tempfile.TemporaryDirectory(prefix="galbench-check-") as directory:
                root=Path(directory)
                spec=SPECS[config.provider];module=adapter(config.provider)
                if spec.get("acp_all") or config.supports_vision and spec.get("acp_images"):
                    module.acp_invocation(config.provider,config,root,self.capabilities)
                else: module.invocation(config.provider,config,root,"preflight",[],self.capabilities)

    def info(self):
        info = {"provider": self.config.provider, "executable": str(self.path), "version": self.version,
                "login": AGENTS[self.config.provider]["login"], "model_request_sent": False,
                "capabilities":sorted(self.capabilities)}
        if self.launch_environment.get("ELECTRON_RUN_AS_NODE")=="1":info["launch_runtime"]="electron_node"
        if self._probe_result is not None:info["runtime_check"]=self._probe_result
        return info

    def check_runtime(self, *, require_images=False):
        """DSH: validate real startup once, without sending session/prompt.

        Shared by concurrent answer/judge jobs. Startup errors prevent broken
        calls; an absent image declaration is only an advisory for the tester.
        """
        if self.config.provider != "deepseek_harness":return self.info()
        from .acp import complete_acp, image_capability_warnings
        with self._probe_lock:
            if self._probe_error is not None:raise self._probe_error
            if self._probe_result is None:
                try:
                    with tempfile.TemporaryDirectory(prefix="galbench-acp-check-") as directory:
                        root=Path(directory)
                        args,environment=adapter(self.config.provider).acp_invocation(self.config.provider,self.config,root,self.capabilities)
                        self._probe_result=complete_acp([*self.command,*args],self.config,root,"",[],
                            {**self.launch_environment,**environment},probe=True,require_images=require_images)
                        if self.config.cli_model:self._probe_result["configured_route"]=self.config.cli_model
                except APIError as exc:
                    exc.fatal=True;self._probe_error=exc
                    raise
            if require_images:
                warnings=self._probe_result.setdefault("warnings",[])
                for warning in image_capability_warnings(self._probe_result.get("agent_capabilities",{}),True):
                    if warning not in warnings:warnings.append(warning)
        return self.info()

    def complete(self, messages):
        started = time.monotonic()
        # Only public messages/images enter this directory. A fresh directory does
        # not itself deny OS file access; CLI scores use a separate protocol.
        with tempfile.TemporaryDirectory(prefix="galbench-agent-") as directory:
            root = Path(directory)
            prompt, images = self._input(messages, root)
            self.check_runtime(require_images=bool(images))
            spec = SPECS.get(self.config.provider,{})
            if spec.get("acp_all") or images and spec.get("acp_images"):
                from .acp import complete_acp
                args, environment = adapter(self.config.provider).acp_invocation(self.config.provider,self.config,root,self.capabilities)
                response = complete_acp([*self.command,*args],self.config,root,prompt,images,{**self.launch_environment,**environment})
                err = ""
                policy = spec.get("acp_tool_policy","disabled_and_audited")
            else:
                environment = {}
                if self.config.provider in SPECS:
                    args,stdin,environment = adapter(self.config.provider).invocation(self.config.provider,self.config,root,prompt,images,self.capabilities)
                    command = [*self.command,*args]
                else: command, stdin = self._invocation(root, prompt, images)
                code, out, err = execute(command, cwd=root, timeout=self.config.timeout_seconds, stdin=stdin,
                                         credential_env=self.config.credential_env,environment={**self.launch_environment,**environment})
                if code: raise execution_error(code, err, out)
                try:response = parse_cli_output(self.config.provider, out)
                except APIError as exc:
                    if err:exc.details={**(exc.details or {}),"stderr_preview":redact_diagnostic(err[-6000:])}
                    raise
                policy = "disabled_and_audited"
        return {**response, "elapsed_seconds": time.monotonic() - started, "http_attempts": 0,
                "agent": {"provider": self.config.provider, "version": self.version,
                          "adapter_version": ADAPTER_VERSION, "fresh_session": True,
                          "completion_evidence":response.get("completion_evidence","cli_success_result"),
                          "stderr_present": bool(err), "tool_policy": policy}}

    def _input(self, messages, root):
        parts, images = [], []
        for message in messages:
            role = message.get("role")
            if role not in {"system", "user"}: raise ValueError("CLI evaluation requires fresh system/user messages")
            content = message.get("content")
            texts = []
            if isinstance(content, str): texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text": texts.append(part["text"])
                    elif part.get("type") == "image_url":
                        if not self.config.supports_vision: raise ValueError("CLI model image support is disabled")
                        match = re.fullmatch(r"data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)", part["image_url"]["url"])
                        if not match: raise ValueError("CLI only accepts bundled inline image inputs")
                        path = root / f"image-{len(images)+1}.{match[1]}"
                        path.write_bytes(base64.b64decode(match[2], validate=True)); images.append(path)
                    else: raise ValueError("Unsupported CLI message content")
            else: raise ValueError("Unsupported CLI message content")
            parts.append(f"<{role}>\n" + "\n".join(texts) + f"\n</{role}>")
        instruction = ("这是独立的 galbench 测试。只根据题目与已附图片作答，不调用工具，不读其他文件，"
                       "不联网，不恢复历史会话。仅输出题目要求的最终答案。以下 system 段为评分任务要求。\n")
        return instruction + "\n\n".join(parts), images

    def _invocation(self, root, prompt, images):
        provider = self.config.provider
        if provider == "codex":
            args = ["exec", "--json", "--ephemeral", "--skip-git-repo-check",
                    "--sandbox", "read-only", "--color", "never", "-C", str(root),
                    "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
                    "--disable", "shell_tool", "--disable", "unified_exec"]
            if not self.config.use_client_config: args += ["--ignore-user-config"]
            if self.config.cli_profile: args += ["--profile", self.config.cli_profile]
            if self.config.cli_model: args += ["--model", self.config.cli_model]
            for path in images: args += ["--image", str(path)]
            return [*self.command, *args, "-"], prompt
        if provider == "minimax_code":
            args = ["exec", "--input", "-", "--mode", "lightweight", "--output-format", "stream-json",
                    "--cwd", str(root), "--permission", "smart"]
            if self.config.cli_model: args += ["--model", self.config.cli_model]
            for path in images: args += ["--file", str(path)]
            return [*self.command, *args], prompt
        (root / "zcode.json").write_text(json.dumps({
            "features": {"mcp": False, "subagent": False, "memory": False, "skill": False},
            "memory": {"use": False}, "plugins": {"enabled": False},
            "skills": {"enabled": False}, "hooks": {"enabled": False}}), encoding="utf-8")
        blocked = ("Bash Read Write Edit MultiEdit Glob Grep WebSearch WebFetch BrowserUse ComputerUse "
                   "Agent Task Skill AskUserQuestion EnterPlanMode ExitPlanMode NotebookEdit TodoWrite")
        args = ["--prompt", prompt, "--cwd", str(root), "--mode", "plan", "--disallowed-tools", blocked,
                "--workflow-mode", "disabled", "--output-format", "stream-json", "--no-color"]
        for path in images: args += ["--attach", str(path)]
        if os.name == "nt" and len(subprocess.list2cmdline([*self.command, *args])) > 30000:
            raise ValueError("ZCode 的 --prompt 超过 Windows 命令行长度上限；请缩短片段或改用 API/Codex/MiniMax 通道")
        return [*self.command, *args], ""
