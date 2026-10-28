"""Auditable, response-bound manual scores; never alter generation records."""
from __future__ import annotations

import math
from .data import fingerprint
from .scoring import stage_maximum


def context(q, repeat, stage, prediction, evaluation_hash):
    return {"question_id": q.id, "repeat": repeat, "stage": stage,
            "evaluation_hash": evaluation_hash, "rubric_sha256": q.rubric_signature,
            "prediction_sha256": fingerprint(prediction)}


def validate_submission(record):
    value = record.get("score_percent")
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("人工得分须为 0–100 的有限数值")
    if not isinstance(record.get("reason"), str) or not 1 <= len(record["reason"].strip()) <= 2000:
        raise ValueError("请填写 1–2000 字的人工评分依据")
    if not isinstance(record.get("replacement_answer", ""), str) or len(record.get("replacement_answer", "")) > 20000:
        raise ValueError("补录回答须为不超过 20000 字的文本")


def matches(record, expected):
    return all(record.get(k) == v for k, v in expected.items())


def apply(q, stage, original, record):
    validate_submission(record)
    maximum = stage_maximum(q, stage)
    ratio = record["score_percent"] / 100
    value = {**original, "original_assessment": original.get("original_assessment", dict(original)),
             "status": "C" if ratio == 1 else "W" if ratio == 0 else "P",
             "points": maximum * ratio, "max_points": maximum, "item_score": ratio,
             "method": "manual_score", "scoring_state": "scored", "manual_score": record,
             "reason": record["reason"], "dimension_scores": {}}
    return value


def original_stage(stage):
    return stage.get("original_assessment", stage)


def has_execution_issue(row):
    return any(original_stage(s).get("status") == "ERROR" for s in row.get("stages", {}).values()) or row.get("status") == "ERROR"


def has_manual_score(row):
    return any(s.get("method") == "manual_score" for s in row.get("stages", {}).values())
