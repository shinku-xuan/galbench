"""Conservative extraction; JSON compliance is separate from content credit."""
from __future__ import annotations
import json
import re
import unicodedata
from dataclasses import dataclass


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", "", value.strip(" \t\r\n。.!！?？\"'“”‘’"))


def parse_object(text):
    value = text.strip().lstrip("\ufeff")
    if value.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", value, re.S | re.I)
        if not match: raise ValueError("Invalid JSON fence")
        value = match.group(1).strip()
    def reject(value): raise ValueError("Non-finite JSON number")
    parsed = json.loads(value, parse_constant=reject)
    if not isinstance(parsed, dict): raise ValueError("Expected one JSON object")
    return parsed


@dataclass
class Parsed:
    value: dict
    format_compliant: bool
    status: str | None = None
    reason: str = ""


ABSTENTIONS = {normalize(s) for s in ("不知道", "不清楚", "无法确定", "不确定", "我不知道", "I don't know", "unknown")}


def parse_response(response, stage="answer"):
    if response.get("execution_state") == "error" or response.get("error"):
        return Parsed({}, False, "ERROR", "execution_error")
    if response.get("refusal") or response.get("finish_reason") == "content_filter":
        return Parsed({}, False, "A", "provider_refusal")
    if response.get("finish_reason") == "length":
        return Parsed({}, False, "ERROR", "output_limit_reached")
    if response.get("finish_reason") not in {None, "stop"}:
        return Parsed({}, False, "V", "non_final_or_truncated_response")
    text = response.get("text", "")
    if not isinstance(text, str) or not text.strip(): return Parsed({}, False, "V", "empty_output")
    try:
        value = parse_object(text)
        compliant = True
    except ValueError:
        if text.lstrip().startswith(("{", "[", "```")):
            return Parsed({}, False, "V", "unparseable_structured_output")
        if normalize(text) in ABSTENTIONS: return Parsed({}, False, "A", "explicit_abstention")
        compliant = False
        if stage == "b":
            fields = {}
            for label, key in (("作品", "work_guess"), ("游戏", "work_guess"), ("角色", "characters"), ("人物", "characters"), ("后续", "continuation"), ("预测", "continuation")):
                match = re.search(rf"(?:^|\n)\s*{label}\s*[:：]\s*(.*?)(?=\n\s*(?:作品|游戏|角色|人物|后续|预测)\s*[:：]|\Z)", text, re.S)
                if match: fields[key] = match.group(1).strip()
            if not fields: return Parsed({}, False, "V", "unextractable_inference")
            value = {"work_guess": fields.get("work_guess", ""),
                     "characters": [s.strip() for s in re.split(r"[、,，;；]", fields.get("characters", "")) if s.strip()],
                     "continuation": [{"event": s.strip("- •0123456789.、"), "basis": "unknown"}
                                      for s in fields.get("continuation", "").splitlines() if s.strip()], "abstain": False}
        else:
            value = {"summary" if stage == "a" else "answer": text.strip(), "abstain": False}
    abstain = value.get("abstain", False)
    compliant = compliant and "abstain" in value
    if type(abstain) is not bool: return Parsed({}, False, "V", "abstain_must_be_boolean")
    if stage == "b":
        defaults = {"work_guess": "", "characters": [], "continuation": []}
        compliant = compliant and all(k in value for k in defaults)
        value = {**defaults, **value}
        if (not isinstance(value["work_guess"], str) or not isinstance(value["characters"], list)
                or not all(isinstance(s, str) and s.strip() for s in value["characters"])
                or not isinstance(value["continuation"], list)):
            return Parsed({}, False, "V", "invalid_inference_fields")
        for event in value["continuation"]:
            if not isinstance(event, dict) or not isinstance(event.get("event"), str) or not event["event"].strip():
                return Parsed({}, False, "V", "invalid_prediction_event")
            if event.get("basis") not in {"inference", "remembered", "unknown"}:
                event["basis"] = "unknown"; compliant = False
        if len(value["continuation"]) > 3: compliant = False
        nonempty = bool(value["work_guess"].strip() or value["characters"] or value["continuation"])
    else:
        key = "summary" if stage == "a" else "answer"
        if not isinstance(value.get(key), str): return Parsed({}, False, "V", "answer_must_be_text")
        nonempty = bool(value[key].strip())
        if key == "answer" and value.get("work_guess") is not None and not isinstance(value["work_guess"], str):
            return Parsed({}, False, "V", "work_guess_must_be_text")
    if abstain and nonempty: return Parsed(value, False, "V", "abstention_with_answer")
    if abstain or (stage == "b" and not nonempty): return Parsed(value, compliant, "A", "explicit_abstention")
    if not nonempty: return Parsed(value, compliant, "V", "empty_attempted_answer")
    return Parsed(value, compliant)
