"""Official OpenCode and DeepSeek Harness process protocols.

Sources are the vendors' CLI documentation and public CLI/event implementations.
The generated configuration belongs to one disposable evaluation directory; no
user profile, authentication document, or global model selection is rewritten.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..api import APIError

SPECS = {
    "opencode": {
        "label": "OpenCode", "command": "opencode", "login": "opencode auth login",
        "docs": "https://dev.opencode.ai/v2/docs/cli/commands/",
        "help_args": ["run", "--help"], "model_override": True, "image_input": True,
        "notes": "使用私有服务与无工具 galbench Agent。模型填 provider/model；图片由 --file 真正附加。v2 需要 --standalone；v1 需支持 --dir。",
    },
    "deepseek_harness": {
        "label": "DeepSeek Harness", "command": "dsh", "login": "dsh web",
        "docs": "https://deepseek-harness.github.io/deepseek-harness/en/guide/quickstart",
        "help_args": ["--help"],
        "model_override": True, "image_input": True, "acp_images": True, "acp_all": True,
        "notes": "模型调用 ID 填 provider/model（如 deepseek-official/deepseek-flash），只覆盖本次 ACP 会话；留空沿用 ACP profile，可能不同于 Web 当前模型。ACP 图片声明仅供参考，勾选支持图片后会实际尝试。",
    },
}


def _require(capabilities, flags, label):
    missing = sorted(set(flags) - set(capabilities))
    if missing:
        raise ValueError(f"{label} CLI 不支持所需参数 {' / '.join(missing)}；请检查安装版本")


def _opencode_config(root: Path, *, v2: bool):
    """Agent-local denials take precedence over the user's default Agent policy."""
    directory = root / ".opencode"
    directory.mkdir(exist_ok=True)
    if v2:
        deny = [{"action": "*", "resource": "*", "effect": "deny"}]
        value = {"permissions": deny, "agents": {"galbench": {
            "description": "Closed-book galbench evaluation without tools", "mode": "primary",
            "system": "Answer the supplied galbench task directly. Do not use tools, files, web access, or other sessions.",
            "permissions": deny,
        }}}
    else:
        value = {"permission": {"*": "deny"}, "agent": {"galbench": {
            "description": "Closed-book galbench evaluation without tools", "mode": "primary",
            "prompt": "Answer the supplied galbench task directly. Do not use tools, files, web access, or other sessions.",
            "permission": {"*": "deny"}, "tools": {"*": False},
        }}}
    (directory / "opencode.json").write_text(json.dumps(value), encoding="utf-8")


_NO_TOOLS = """// Per-invocation policy using the public Cordis scoped tool API.
export const name = 'galbench-closed-book';
export const inject = ['tools'];
export function apply(ctx) {
  ctx.tools.guard(() => 'galbench closed-book evaluation forbids all tools');
  ctx.on('agent/created', ({agent}) => {
    agent.ctx.tools.presentAs('native');
    agent.ctx.tools.restrict({allow: []});
    process.stderr.write('GALBENCH_SESSION_POLICY_READY\\n');
  });
  process.stderr.write('GALBENCH_POLICY_READY\\n');
}
"""

# DSH's account catalog uses console.info, including during session/new. ACP
# uses process.stdout.write directly; redirect diagnostics before any plugin
# loads, while keeping the machine protocol strict. Do not apply to other CLIs.
_STDIO_PRELOAD = """import {Console} from 'node:console';
const diagnostics = new Console({stdout: process.stderr, stderr: process.stderr});
for (const method of ['log', 'info', 'debug', 'warn', 'error', 'dir', 'table', 'trace', 'assert', 'time', 'timeLog', 'timeEnd', 'count', 'countReset']) {
  console[method] = diagnostics[method].bind(diagnostics);
}
"""


def _deepseek_overlay(root: Path, route=""):
    """Mask global tools on each new Agent and deny all execution globally."""
    (root / "galbench-no-tools.mjs").write_text(_NO_TOOLS, encoding="utf-8")
    disabled = (
        "tool-executor-native", "tool-executor-ptc", "tool-working-directory",
        "tool-bash", "tool-pwsh", "tool-jobs", "tool-fs", "tool-fs-search", "tool-skill",
        "tool-subagent-control", "tool-subagent", "tool-subagent-fork", "tool-workflow",
        "tool-todo", "tool-goal", "tool-web", "agent-instructions", "skill-filesystem",
        "session-title-llm",
    )
    rows = [{"id": name, "disabled": True} for name in disabled]
    # Headless disables these shipped services. Restore them locally so an
    # explicitly configured account route can reuse its own official sign-in.
    rows += [{"id": name, "disabled": False} for name in ("credentials", "authorization", "deepseek-account")]
    rows += [
        {"id": "tools", "config": {"mode": "native"}},
        {"id": "sandbox-policy", "config": {"mode": "read-only", "workspaceRoot": str(root)}},
        {"id": "approval", "config": {"policy": "never"}},
        {"id": "permission", "config": {"presets": {"galbench": {"sandbox": "read-only", "approval": "never"}}, "defaultPreset": "galbench"}},
        {"insert": [{"id": "galbench-no-tools", "name": "./galbench-no-tools.mjs", "inject": ["tools"]}]},
    ]
    if route:
        provider, separator, model = route.partition("/")
        if not separator or not provider.strip() or not model.strip() or any(c.isspace() for c in route):
            raise ValueError("DeepSeek Harness 模型须填写 provider/model，例如 deepseek-official/deepseek-flash；留空沿用 ACP profile")
        rows.append({"id": "acp", "config": {"provider": provider, "model": model}})
    # JSON is a YAML 1.2 subset; quotes safely preserve Windows paths and Unicode.
    patch = root / "galbench-closed-book.patch.yml"
    patch.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return patch


def invocation(provider, config, root: Path, prompt: str, images: list[Path], capabilities: set[str]):
    root = Path(root)
    if provider == "opencode":
        _require(capabilities, {"--format", "--agent"}, "OpenCode")
        if config.cli_model:
            _require(capabilities, {"--model"}, "OpenCode")
            if "/" not in config.cli_model or config.cli_model.startswith("/") or config.cli_model.endswith("/"):
                raise ValueError("OpenCode 模型须填写 provider/model")
        if images:
            _require(capabilities, {"--file"}, "OpenCode")
            if not config.supports_vision:
                raise ValueError("请先确认 OpenCode 当前模型支持图片输入")
        v2 = "--standalone" in capabilities
        if not v2 and "--dir" not in capabilities:
            raise ValueError("无法确认 OpenCode 私有运行模式；需要 v2 --standalone 或 v1 --dir")
        _opencode_config(root, v2=v2)
        args = ["run", "--format", "json", "--agent", "galbench"]
        if v2:
            args += ["--standalone"]
        else:
            args += ["--dir", str(root)]
        if config.cli_model:
            args += ["--model", config.cli_model]
        for image in images:
            args += ["--file", str(image)]
        # No positional prompt: both official run implementations consume stdin.
        # No --attach/--server/--continue/--session: this invocation owns its run.
        return args, prompt, {}
    if provider == "deepseek_harness":
        if images:
            raise ValueError("DeepSeek headless 只接受文本；识图必须使用官方 ACP profile")
        if config.cli_model:
            raise ValueError("DeepSeek Harness 模型由 profile 配置选择，不支持 --model 覆盖")
        _require(capabilities, {"--profile", "--patch", "--json", "--permission-mode", "--approval-policy"}, "DeepSeek Harness")
        patch = _deepseek_overlay(root)
        return ["--profile", "headless", "--patch", str(patch), "--json",
                "--permission-mode", "read-only", "--approval-policy", "never", "-"], prompt, {}
    raise ValueError("Unsupported Harness provider")


def acp_invocation(provider, config, root: Path, capabilities: set[str]):
    if provider != "deepseek_harness":
        raise ValueError("This module only owns the DeepSeek ACP profile")
    _require(capabilities, {"--profile", "--patch"}, "DeepSeek Harness")
    root = Path(root)
    patch = _deepseek_overlay(root, config.cli_model)
    preload = root / "galbench-acp-stdio.mjs"
    preload.write_text(_STDIO_PRELOAD, encoding="utf-8")
    return ["--profile", "acp", "--patch", str(patch)], {"NODE_OPTIONS": '--import="' + preload.resolve().as_uri() + '"'}


def _usage_sum(samples):
    """Only publish totals when every observed step supplied the required usage."""
    from ..clients import normalize_usage
    if not samples or any(not isinstance(sample, dict) for sample in samples):
        return {}
    normalized = [normalize_usage(sample) for sample in samples]
    required = {"prompt_tokens", "completion_tokens"}
    if any(not required.issubset(sample) for sample in normalized):
        return {}
    result = {key: sum(sample[key] for sample in normalized) for key in required}
    result["total_tokens"] = sum(sample.get("total_tokens", sample["prompt_tokens"] + sample["completion_tokens"]) for sample in normalized)
    reasoning = [sample.get("completion_tokens_details", {}).get("reasoning_tokens") for sample in normalized]
    if all(type(value) in (int, float) and math.isfinite(value) for value in reasoning):
        result["completion_tokens_details"] = {"reasoning_tokens": sum(reasoning)}
    return result


def _answer(provider, events, text, *, finish="stop", usage=None, warnings=None):
    if not isinstance(text, str) or not text.strip():
        raise APIError("agent_missing_final_answer：CLI 未返回最终正文", code="agent_missing_final_answer")
    return {"text": text, "refusal": None, "finish_reason": finish, "returned_model": None,
            "returned_provider": None, "usage": usage or {}, "warnings": warnings or [],
            "raw_response": {"provider": provider, "events": events}}


def parse(provider, events: list[dict]):
    from ..clients import execution_error, tool_event
    if not events or any(not isinstance(event, dict) for event in events):
        raise APIError("agent_invalid_output：缺少有效 CLI 事件", code="agent_invalid_output")
    if any(tool_event(event) for event in events):
        raise APIError("agent_tool_use_detected：检测到工具调用，本题不纳入知识评分", code="agent_tool_use_detected")
    if any(event.get("type") == "error" for event in events):
        raise execution_error(0, "", json.dumps(events, ensure_ascii=False))
    session_ids = {event.get("sessionId", event.get("sessionID")) for event in events
                   if isinstance(event.get("sessionId", event.get("sessionID")), str)}
    if len(session_ids) > 1:
        raise APIError("agent_invalid_output：CLI 输出混合了多个会话", code="agent_invalid_output")
    if provider == "deepseek_harness":
        final = [event for event in events if event.get("type") == "final"]
        if events[0].get("type") != "session" or not isinstance(events[0].get("sessionId"), str):
            raise APIError("agent_incomplete：DeepSeek Harness 缺少 session 起始事件", code="agent_incomplete")
        if len(final) != 1 or events[-1].get("type") != "final":
            raise APIError("agent_incomplete：DeepSeek Harness 缺少唯一终止 final 事件", code="agent_incomplete")
        if final[0].get("sessionId") != events[0]["sessionId"]:
            raise APIError("agent_invalid_output：DeepSeek Harness 终止事件不属于本次会话", code="agent_invalid_output")
        reason = final[0].get("reason")
        if not isinstance(reason, dict) or reason.get("kind") != "completed":
            raise APIError("agent_incomplete：DeepSeek Harness 未完成本次任务", code="agent_incomplete")
        steps = [event for event in events if event.get("type") == "status" and event.get("phase") == "step_end"]
        usage = _usage_sum([event.get("usage") for event in steps])
        warnings = ["DeepSeek 事件预览已截短；评分使用未截短的 final.text"] if any(event.get("truncated") for event in events[:-1]) else []
        return _answer(provider, events, final[0].get("text"), usage=usage, warnings=warnings)
    if provider == "opencode":
        finishes = [(index, event.get("part")) for index, event in enumerate(events) if event.get("type") == "step_finish"]
        if not finishes or not isinstance(finishes[-1][1], dict):
            raise APIError("agent_incomplete：OpenCode 缺少 step_finish", code="agent_incomplete")
        final_index, final = finishes[-1]
        if any(event.get("type") == "step_start" for event in events[final_index + 1:]):
            raise APIError("agent_incomplete：OpenCode 后续步骤尚未结束", code="agent_incomplete")
        reason = final.get("reason")
        if reason not in {"stop", "length"}:
            raise APIError("agent_incomplete：OpenCode 未正常结束最终步骤", code="agent_incomplete")
        message_id = final.get("messageID")
        texts = []
        starts = [index for index, event in enumerate(events[:final_index]) if event.get("type") == "step_start"]
        answer_events = events if message_id is not None else events[starts[-1]:] if starts else events
        for event in answer_events:
            part = event.get("part")
            if event.get("type") == "text" and isinstance(part, dict) and (message_id is None or part.get("messageID") == message_id):
                if isinstance(part.get("text"), str): texts.append(part["text"])
        samples = []
        for _, part in finishes:
            tokens = part.get("tokens") if isinstance(part, dict) else None
            samples.append({"input_tokens": tokens.get("input"), "output_tokens": tokens.get("output"),
                            "reasoning_output_tokens": tokens.get("reasoning")} if isinstance(tokens, dict) else None)
        return _answer(provider, events, "".join(texts), finish="length" if reason == "length" else "stop", usage=_usage_sum(samples))
    raise ValueError("Unsupported Harness provider")
