"""Headless Qoder and WorkBuddy/CodeBuddy protocol adapters.

Sources: Qoder CLI reference + TypeScript SDK messages; Tencent CodeBuddy CLI
reference, headless input protocol and SDK messages. No login state is read here.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

from ..api import APIError


SPECS = {
    "qoder": {
        "label": "Qoder", "command": "qoder", "login": "qoder login",
        "docs": "https://docs.qoder.com/cli/run-in-scripts",
        "help_args": ["--help"], "model_override": True, "image_input": True,
        "notes": "调用官方 Qoder CLI；隔离用户/项目设置，无工具、无 MCP、新会话。BYOK 请先在 /model 向导配置并填写模型 ID；auto 等档位不能证明底层模型。",
    },
    "workbuddy": {
        "label": "WorkBuddy / CodeBuddy", "command": "codebuddy", "login": "codebuddy",
        "docs": "https://www.workbuddy.cn/docs/cli/headless",
        "help_args": ["--help"], "model_override": True, "image_input": True,
        "notes": "使用腾讯官方 CodeBuddy CLI 协议；WorkBuddy 桌面内置 CLI 与独立 CLI 的登录/配置可能不同，不保证共用桌面订阅。可显式选择 CLI 路径及 CODEBUDDY_CONFIG_DIR 环境变量。",
    },
}

_COMMON_FLAGS = {
    "--print", "--output-format", "--tools", "--strict-mcp-config",
    "--mcp-config", "--no-session-persistence", "--permission-mode",
}
_TIER_ALIASES = {"auto", "lite", "efficient", "performance", "ultimate", "fast", "premium"}


def _require(provider, capabilities, flags):
    missing = sorted(set(flags) - capabilities)
    if missing:
        raise ValueError(f"{SPECS[provider]['label']} CLI 缺少必要参数 {', '.join(missing)}；请更新官方 CLI，不会退回有工具或纯文本模式")


def invocation(provider, config, root: Path, prompt: str, images: list[Path], capabilities: set[str]):
    """Build a single fresh query; safe to call for capability-only preflight."""
    if provider not in SPECS:
        raise ValueError("Unsupported Qoder/WorkBuddy adapter")
    flags = set(_COMMON_FLAGS)
    if config.cli_model:
        flags.add("--model")
    if provider == "qoder":
        flags.update({"--cwd", "--setting-sources"})
        if config.supports_vision or images:
            flags.add("--attachment")
    else:
        flags.update({"--input-format", "--settings"})
    _require(provider, capabilities, flags)
    if images and not config.supports_vision:
        raise ValueError("当前 Agent 模型未启用图片输入")

    mcp = root / "galbench-mcp.json"
    mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
    args = ["--print", "--output-format", "stream-json", "--tools", "",
            "--strict-mcp-config", "--mcp-config", str(mcp),
            "--no-session-persistence", "--permission-mode", "default"]
    if config.cli_model:
        args += ["--model", config.cli_model]
    if provider == "qoder":
        # Login/BYOK are owned by Qoder. Filesystem settings sources (including
        # user hooks) are omitted; an explicit --model avoids a settings default.
        args += ["--cwd", str(root), "--setting-sources", ""]
        for path in images:
            args += ["--attachment", str(path)]
        return args, prompt, {"QODER_MEMORY": "0", "QODER_MEMORY_USER": "0"}

    args += ["--input-format", "stream-json", "--settings", json.dumps({
        "disableAllHooks": True, "memory": {"enabled": False}, "promptSuggestionEnabled": False,
    }, separators=(",", ":"))]
    content = [{"type": "text", "text": prompt}]
    for path in images:
        mime = mimetypes.guess_type(str(path))[0]
        if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise ValueError("WorkBuddy 图片仅支持 PNG/JPEG/GIF/WebP")
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                        "data": base64.b64encode(path.read_bytes()).decode("ascii")}})
    user = {"type": "user", "message": {"role": "user", "content": content}}
    return args, json.dumps(user, ensure_ascii=False) + "\n", {}


def _plain_id(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _assistant_messages(events):
    return [e["message"] for e in events if e.get("type") == "assistant"
            and isinstance(e.get("message"), dict) and e.get("parent_tool_use_id") is None]


def _available_text(events):
    messages = _assistant_messages(events)
    for message in reversed(messages):
        content = message.get("content", [])
        if isinstance(content, list):
            text = "".join(p["text"] for p in content if isinstance(p, dict)
                           and p.get("type") == "text" and isinstance(p.get("text"), str))
            if text.strip():
                return text
    return None


def parse(provider, events: list[dict]):
    """Only a terminal success result is an answer; thinking/tools are excluded."""
    from ..clients import execution_error, normalize_usage, tool_event

    if provider not in SPECS or not events or any(not isinstance(e, dict) for e in events):
        raise APIError("agent_invalid_output：缺少有效的 CLI JSON 事件")
    if any(tool_event(e) for e in events):
        raise APIError("agent_tool_use_detected：检测到工具调用，本题不纳入知识评分")
    results = [e for e in events if e.get("type") == "result"]
    if len(results) != 1:
        raise APIError("agent_incomplete：CLI 未返回唯一的最终 result，本题不能自动评分")
    result = results[0]
    if (result.get("subtype") != "success" or result.get("is_error") is not False
            or any(e.get("type") in {"error", "turn.failed"} for e in events)):
        error = execution_error(0, "", json.dumps(events, ensure_ascii=False))
        available = _available_text(events)
        if available:
            error.details["available_answer"] = available
        raise error
    text = result.get("result")
    if not isinstance(text, str) or not text.strip():
        raise APIError("agent_missing_final_answer：CLI 成功结果中没有最终正文")

    assistants = _assistant_messages(events)
    models = {_plain_id(m.get("model")) for m in assistants}
    model_usage = result.get("modelUsage")
    if isinstance(model_usage, dict):
        models.update(_plain_id(m) for m in model_usage)
    models.discard(None)
    warnings = []
    if provider == "qoder":
        models = {m for m in models if m.casefold() not in _TIER_ALIASES}
        if not models:
            initial = [_plain_id(e.get("model")) for e in events
                       if e.get("type") == "system" and e.get("subtype") == "init"]
            models.update(m for m in initial if m and m.casefold() not in _TIER_ALIASES)
            if models:
                warnings.append("CLI 仅回传初始化模型名称，未回传逐请求模型身份")
        if not models:
            warnings.append("Qoder 未回传可确认的模型 ID；自动档位不是底层模型名称")
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "api_retry":
            status = event.get("error_status")
            # Do not copy the raw upstream error text into a warning.
            warnings.append(f"CLI 已恢复一次模型请求重试（HTTP {status if type(status) is int else '未知'}）")
    if len(models) > 1:
        warnings.append("本题 CLI 回传多个模型 ID，保留混合模型身份，不推断唯一模型")
    finish = "length" if assistants and assistants[-1].get("stop_reason") in {"max_tokens", "length"} else "stop"
    return {"text": text, "refusal": None, "finish_reason": finish,
            "returned_model": next(iter(models)) if len(models) == 1 else None,
            "returned_models": sorted(models), "returned_provider": None,
            "usage": normalize_usage(result.get("usage")), "warnings": warnings,
            "raw_response": {"provider": provider, "events": events}}
