"""galbench rubric 1.0: judges annotate facts; Python computes every score."""
from __future__ import annotations
import math
import re
from .data import fingerprint
from .judging import JudgmentService, PendingReview
from .responses import normalize, parse_object, parse_response

SCORER_VERSION = "galbench-rubric-engine-2.0-fixed"
DIMS_A = ("coverage", "relations", "faithfulness", "expression")
DIMS_B = ("work_identification", "character_identification", "prediction_grounding", "prediction_match")
DIMS = DIMS_A + DIMS_B

def stage_maximum(q, stage):
    if stage == "answer": return 1
    if stage == "a": return 100 if q.summary_only else 80
    return 20

def _status(score, maximum):
    return "C" if math.isclose(score, maximum, abs_tol=1e-9) else "P" if score > 0 else "W"

def _out(points, maximum, parsed, *, status=None, reason="", **extra):
    return {"status": status or _status(points, maximum), "points": points, "max_points": maximum,
            "item_score": None if points is None else points / maximum,
            "format_compliant": parsed.format_compliant, "reason": reason,
            "answer": parsed.value, "scorer_version": SCORER_VERSION, **extra}

def _map(value, units, name):
    expected = {u["id"] for u in units}
    if not isinstance(value, dict) or set(value) != expected or any(type(v) is not bool for v in value.values()):
        raise ValueError(f"{name}: expected all rubric IDs with boolean values")

def _int(value, low, high):
    if type(value) is not int or not low <= value <= high: raise ValueError("Out-of-range judge rating")

def _quote(quote, candidate):
    if not isinstance(quote, str) or not normalize(quote) or normalize(quote) not in normalize(candidate):
        raise ValueError("Judge support must quote the actual candidate answer")

def _support(obs, maps, candidate):
    support = obs.get("support")
    if not isinstance(support, dict): raise ValueError("Missing quote support")
    for mapping in maps:
        for key, active in mapping.items():
            if active: _quote(support.get(key), candidate)

def _unique_errors(items):
    """One error quote gets the highest applicable deduction, not duplicate penalties."""
    selected = []
    for item in sorted(items, key=lambda v: v["deduction"], reverse=True):
        quote = normalize(item["quote"])
        if any(quote in normalize(old["quote"]) or normalize(old["quote"]) in quote for old in selected):
            continue
        selected.append(item)
    return selected

def _entity_text(value):
    value = normalize(value).replace("・", "·")
    pairs = {'《': '》', '「': '」', '『': '』', '“': '”', '"': '"', "'": "'"}
    while len(value) >= 2 and pairs.get(value[0]) == value[-1]: value = value[1:-1].strip()
    return normalize(value)


def entity_match(answer, entity):
    accepted = {_entity_text(a) for a in [entity["canonical"], *entity["aliases"]]}
    answer = re.sub(r"^(?:答案|人物|角色|答)\s*[:：]\s*", "", answer.strip())
    answer = re.sub(r"^(?:答案|答)\s*(?:是|为)\s*[:：]?\s*", "", answer)
    answer = re.sub(r"^(?:(?:图中|图片中|画面中)的?(?:人物|角色)|(?:这|该)(?:位|名)?(?:人物|角色)|人物|角色)\s*(?:是|为)\s*", "", answer)
    answer = re.sub(r"的角色(?:主题|曲|歌)[。.!！]?\s*$", "", answer)
    parts = [p for p in re.split(r"\s*[/、,，;；()（）]\s*|\s+or\s+|或", answer) if p.strip()]
    return bool(parts) and all(_entity_text(p) in accepted for p in parts)


def grading_route(q):
    if q.scoring["type"] in {"exact_entity", "portrait_identity"}: return "rules"
    if q.board == "story": return "semantic"
    return "hybrid"

def _semantics(service, q, repeat, stage, kind, payload, shape, validator, optional=False):
    return service.evaluate(kind=kind, question=q, repeat=repeat, stage=stage,
                            payload=payload, shape=shape, validate=validator, optional=optional)

def grade_answer(q, response, service, repeat):
    parsed = parse_response(response)
    if parsed.status: return _out(0, 1, parsed, status=parsed.status, reason=parsed.reason, method="extraction")
    answer = parsed.value["answer"]
    scoring = q.scoring
    if scoring["type"] in {"exact_entity", "portrait_identity"}:
        if entity_match(answer, scoring["answer"]):
            result = _out(1, 1, parsed, method="rules", reason="accepted_entity")
        else:
            result = _out(0, 1, parsed, method="rules", reason="not_in_verified_aliases")
        if q.board == "vision":
            guess = parsed.value.get("work_guess", "")
            result["work_guess_correct"] = (normalize(guess) in {normalize(s) for s in
                [q.private["work"]["title"], *q.private["work"]["aliases"]]} if guess else None)
        return result
    facts, contradictions = scoring["facts"], scoring.get("contradictions", [])
    if normalize(answer) == normalize(scoring.get("reference_answer", "")):
        return _out(1, 1, parsed, method="rules", fact_hits={f["id"]: True for f in facts},
                    triggered_contradictions=[], evidence_ids=[e["id"] for e in q.private["evidence"]])
    payload = {"candidate": answer, "question": q.row["input"], "facts": facts,
               "contradictions": contradictions, "evidence": q.private["evidence"]}
    shape = {"needs_review": False, "fact_hits": {f["id"]: "boolean" for f in facts},
             "contradictions": {c["id"]: "boolean" for c in contradictions},
             "support": "object: each true ID -> literal quote from candidate", "reason": "string"}
    def validate(obs):
        _map(obs.get("fact_hits"), facts, "fact_hits")
        if not contradictions:
            # Unregistered deductions cannot affect the score. Keep an audit of
            # unsolicited annotations instead of rejecting otherwise valid hits.
            extra = obs.get("contradictions")
            if extra:
                obs["normalizations"] = [{"field": "contradictions", "ignored_unregistered": extra}]
            obs["contradictions"] = {}
        _map(obs.get("contradictions"), contradictions, "contradictions")
        _support(obs, [obs["fact_hits"], obs["contradictions"]], answer)
    obs, meta = _semantics(service, q, repeat, "answer", "atomic", payload, shape, validate)
    errors = _unique_errors([{"id": c["id"], "deduction": c["deduction"], "quote": obs["support"][c["id"]]}
                             for c in contradictions if obs["contradictions"][c["id"]]])
    score = max(0, min(1, sum(f["weight"] for f in facts if obs["fact_hits"][f["id"]]) - sum(c["deduction"] for c in errors)))
    return _out(score, 1, parsed, fact_hits=obs["fact_hits"], triggered_contradictions=errors,
                evidence_ids=[e["id"] for e in q.private["evidence"]], reason=obs.get("reason", ""), **meta)

def grade_summary(q, response, service, repeat):
    maximum = stage_maximum(q, "a")
    parsed = parse_response(response, "a")
    if parsed.status:
        return _out(0, maximum, parsed, status=parsed.status, reason=parsed.reason,
                    dimension_scores=dict.fromkeys(DIMS_A, 0), method="extraction")
    candidate = parsed.value["summary"]
    rubric = q.scoring["summary"]
    facts, relations, critical = rubric["coverage_facts"], rubric["causal_relations"], rubric.get("critical_errors", [])
    payload = {"candidate": candidate, "excerpt": q.excerpt(), "rubric": rubric,
               "rubric_notes": "只依据片段；未出现的真实后续也是新增事实。表达的clarity/organization/compression各0至5，按0/3/5锚点评分。"}
    shape = {"needs_review": False, "coverage_hits": {f["id"]: "boolean" for f in facts},
             "relation_hits": {r["id"]: "boolean" for r in relations},
             "critical_errors": {c["id"]: "boolean" for c in critical},
             "unsupported_facts": [{"quote": "literal candidate quote", "claim": "one unsupported claim"}],
             "expression": {"clarity": "integer 0..5", "organization": "integer 0..5", "compression": "integer 0..5"},
             "support": "object: each true rubric ID -> literal quote", "reason": "string"}
    def validate(obs):
        for field, units in (("coverage_hits", facts), ("relation_hits", relations), ("critical_errors", critical)):
            _map(obs.get(field), units, field)
        _support(obs, [obs["coverage_hits"], obs["relation_hits"], obs["critical_errors"]], candidate)
        expression = obs.get("expression")
        if not isinstance(expression, dict) or set(expression) != {"clarity", "organization", "compression"}:
            raise ValueError("Invalid expression axes")
        for rating in expression.values(): _int(rating, 0, 5)
        extras = obs.get("unsupported_facts")
        if not isinstance(extras, list): raise ValueError("unsupported_facts must be an array")
        for extra in extras:
            if not isinstance(extra, dict) or not isinstance(extra.get("claim"), str): raise ValueError("Invalid unsupported fact")
            _quote(extra.get("quote"), candidate)
    obs, meta = _semantics(service, q, repeat, "a", "summary", payload, shape, validate)
    errors = [{"id": c["id"], "deduction": c["deduction"], "quote": obs["support"][c["id"]]}
              for c in critical if obs["critical_errors"][c["id"]]]
    errors += [{"id": f"unsupported-{i}", "deduction": 5 if q.summary_only else 4, "quote": e["quote"]}
               for i, e in enumerate(obs["unsupported_facts"])]
    errors = _unique_errors(errors)
    weights = (30, 25, 25, 20) if q.summary_only else (25, 20, 20, 15)
    dims = {"coverage": weights[0] * sum(f["weight"] for f in facts if obs["coverage_hits"][f["id"]]),
            "relations": weights[1] * sum(r["weight"] for r in relations if obs["relation_hits"][r["id"]]),
            "faithfulness": max(0, weights[2] - sum(e["deduction"] for e in errors)),
            "expression": weights[3] * sum(obs["expression"].values()) / 15}
    caps = []
    if dims["coverage"] == 0:
        dims = dict.fromkeys(DIMS_A, 0); caps.append("no_event_coverage")
    points = sum(dims.values())
    text = re.sub(r"\s+", "", candidate)
    if len(text) > 2 * rubric["length"]["max"]: caps.append("over_twice_max_length")
    compact = lambda v: "".join(ch.casefold() for ch in v if ch.isalnum())
    raw = compact(candidate)
    versions = [compact("".join(t["text"] for t in q.excerpt())),
                compact("".join(t["speaker"] + t["text"] for t in q.excerpt()))]
    if raw and any(raw in original and len(raw) >= .8 * len(original) for original in versions):
        caps.append("continuous_copy")
    if any(c in caps for c in ("over_twice_max_length", "continuous_copy")): points = min(points, maximum / 2)
    return _out(points, maximum, parsed, dimension_scores=dims, summary_uncapped_points=sum(dims.values()),
                summary_length_chars=len(text), summary_length_status=("below_min" if len(text) < rubric["length"]["min"] else "above_max" if len(text) > rubric["length"]["max"] else "within_range"),
                summary_caps=caps, fact_hits=obs["coverage_hits"], relation_hits=obs["relation_hits"],
                triggered_contradictions=errors, expression_axes=obs["expression"],
                reason=obs.get("reason", ""), evidence_ids=[e["id"] for e in q.private["evidence"]], **meta)

def _character_score(names, entities, mappings=None):
    gold = {e["entity_id"] for e in entities}
    hits, wrong = set(), set()
    for i, name in enumerate(names):
        matched = next((e["entity_id"] for e in entities if entity_match(name, e)), None)
        if matched is None and mappings is not None: matched = mappings[str(i)]
        if matched: hits.add(matched)
        else: wrong.add(normalize(name))
    return 5 * (2 * len(hits) / (2 * len(hits) + len(wrong) + len(gold - hits))) if names else 0

def grade_inference(q, response, service, repeat):
    parsed = parse_response(response, "b")
    if parsed.status:
        return _out(0, 20, parsed, status=parsed.status, reason=parsed.reason,
                    dimension_scores=dict.fromkeys(DIMS_B, 0), method="extraction")
    candidate, rubric = parsed.value, q.scoring
    identity, prediction = rubric["identification"], rubric["prediction"]
    work_match = normalize(candidate["work_guess"]) in {normalize(a) for a in identity["work_answers"]}
    dims = {"work_identification": 5 * int(work_match),
            "character_identification": _character_score(candidate["characters"], identity["character_entities"]),
            "prediction_grounding": 0, "prediction_match": 0}
    if not candidate["continuation"]:
        return _out(sum(dims.values()), 20, parsed, dimension_scores=dims, method="rules")
    alternatives = prediction["alternatives"]
    entity_ids = {e["entity_id"] for e in identity["character_entities"]}
    alt_ids = {a["id"] for a in alternatives}
    payload = {"candidate": candidate, "excerpt": q.excerpt(), "identification": identity, "prediction": prediction,
               "rubric_notes": "拆分每条候选event的全部原子主张；必须覆盖每个prediction_index。只命中一个完整分支，额外正确细节用extras标注；错误/窗口外主张保留并匹配null。证据0-2、因果0-2、边界表达0-1，自报remembered不证明正确。"}
    shape = {"needs_review": False, "work_match": "boolean",
             "character_matches": {str(i): "entity_id or null" for i in range(len(candidate["characters"]))},
             "grounding": {"evidence": "integer 0..2", "causal": "integer 0..2", "boundary": "integer 0..1"},
             "cue_hits": ["supporting cue IDs"],
             "predicted_claims": [{"prediction_index": "input event index, starts at 0", "text": "one atomic claim",
                                  "quote": "literal quote from that event",
                                  "matches": {a["id"]: "event id or null" for a in alternatives},
                                  "extras": {a["id"]: "accepted_extras index or null" for a in alternatives}}],
             "reason": "string"}
    def validate(obs):
        if type(obs.get("work_match")) is not bool or (work_match and not obs["work_match"]): raise ValueError("Invalid work match")
        mapping = obs.get("character_matches")
        if not isinstance(mapping, dict) or set(mapping) != {str(i) for i in range(len(candidate["characters"]))}:
            raise ValueError("Must map every supplied character")
        for i, name in enumerate(candidate["characters"]):
            if mapping[str(i)] is not None and mapping[str(i)] not in entity_ids: raise ValueError("Unknown character entity")
            exact = next((e["entity_id"] for e in identity["character_entities"] if entity_match(name, e)), None)
            if exact and mapping[str(i)] != exact: raise ValueError("Judge contradicted exact character identity")
        grounding = obs.get("grounding")
        if not isinstance(grounding, dict) or set(grounding) != {"evidence", "causal", "boundary"}: raise ValueError("Invalid grounding")
        for key, maximum in (("evidence", 2), ("causal", 2), ("boundary", 1)): _int(grounding[key], 0, maximum)
        cues = obs.get("cue_hits")
        allowed_cues = {c["id"] for c in prediction.get("supporting_cues", [])}
        if not isinstance(cues, list) or not set(cues) <= allowed_cues: raise ValueError("Invalid cue IDs")
        if grounding["evidence"] == 2 and len(set(cues)) < min(2, len(allowed_cues)):
            raise ValueError("Evidence rating lacks supporting cues")
        claims = obs.get("predicted_claims")
        if not isinstance(claims, list) or not claims: raise ValueError("No candidate claims assessed")
        seen = set()
        for claim in claims:
            i = claim.get("prediction_index")
            if type(i) is not int or not 0 <= i < len(candidate["continuation"]): raise ValueError("Invalid prediction index")
            seen.add(i); _quote(claim.get("quote"), candidate["continuation"][i]["event"])
            if not isinstance(claim.get("text"), str) or not claim["text"].strip(): raise ValueError("Invalid atomic claim")
            mapping = claim.get("matches")
            if not isinstance(mapping, dict) or set(mapping) != alt_ids: raise ValueError("Must assess every alternative")
            for alternative in alternatives:
                match = mapping[alternative["id"]]
                if match is not None and match not in {e["id"] for e in alternative["events"]}: raise ValueError("Unknown future event")
            extras = claim.get("extras")
            if not isinstance(extras, dict) or set(extras) != alt_ids: raise ValueError("Invalid extras mapping")
            for alternative in alternatives:
                aid = alternative["id"]
                index = extras[aid]
                if index is not None and (type(index) is not int or not 0 <= index < len(alternative.get("accepted_extras", []))):
                    raise ValueError("Unknown accepted extra fact")
                if index is not None and claim["matches"][aid] is not None:
                    raise ValueError("One claim cannot be both required and optional")
        if seen != set(range(len(candidate["continuation"]))): raise ValueError("A candidate prediction was omitted")
    obs, meta = _semantics(service, q, repeat, "b", "inference", payload, shape, validate)
    dims["work_identification"] = 5 * int(obs["work_match"])
    dims["character_identification"] = _character_score(candidate["characters"], identity["character_entities"], obs["character_matches"])
    dims["prediction_grounding"] = sum(obs["grounding"].values())
    branch_scores = {}
    for alternative in alternatives:
        aid = alternative["id"]
        matched, wrong = set(), set()
        for claim in obs["predicted_claims"]:
            if claim["matches"][aid] is not None: matched.add(claim["matches"][aid])
            elif claim["extras"][aid] is None: wrong.add(normalize(claim["text"]))
        tp, fp, fn = len(matched), len(wrong), len(alternative["events"]) - len(matched)
        branch_scores[aid] = {"tp": tp, "fp": fp, "fn": fn, "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0}
    best = max(branch_scores, key=lambda a: branch_scores[a]["f1"])
    dims["prediction_match"] = 5 * branch_scores[best]["f1"]
    return _out(sum(dims.values()), 20, parsed, dimension_scores=dims,
                prediction_claims=obs["predicted_claims"], prediction_alternatives=branch_scores,
                selected_alternative=best, reason=obs.get("reason", ""), **meta)

def grade_stage(q, stage, response, service, repeat=0):
    if stage not in q.stages: raise ValueError("Stage is not part of this question protocol")
    parsed = parse_response(response, stage)
    try:
        result = _out(None, stage_maximum(q, stage), parsed, status="ERROR", reason=parsed.reason, method="execution") if parsed.status == "ERROR" else (
            grade_answer(q, response, service, repeat) if stage == "answer" else (
            grade_summary(q, response, service, repeat) if stage == "a" else grade_inference(q, response, service, repeat))
        )
    except PendingReview as exc:
        parsed = parse_response(response, stage)
        result = _out(None, stage_maximum(q, stage), parsed,
                      status="HOLD", reason=str(exc), method="pending_review")
    result.update({"stage": stage, "judge_version": result.get("judge_version"),
                   "evidence_ids": result.get("evidence_ids", [e["id"] for e in q.private["evidence"]])})
    result["scoring_state"] = assessment_state(result)
    return result


def assessment_state(value):
    if value.get("stages"):
        states = {assessment_state(stage) for stage in value["stages"].values()}
        return next((s for s in ("execution_failed", "not_called", "cancelled", "judge_failed", "needs_review") if s in states), "scored")
    if value.get("reason") == "run_stopped_by_user": return "cancelled"
    if value.get("status") == "ERROR":
        return "not_called" if str(value.get("reason", "")).startswith("batch_aborted") else "execution_failed"
    if value.get("status") == "HOLD":
        reason = str(value.get("reason", ""))
        return "judge_failed" if reason.startswith(("invalid_or_failed_judgment", "judge_output_limit_reached", "judge_refused")) else "needs_review"
    return "scored"

def combine(q, repeat, stages):
    blocked = next((status for status in ("ERROR", "HOLD") if any(s["status"] == status for s in stages.values())), None)
    dimensions = {k: v for s in stages.values() for k, v in s.get("dimension_scores", {}).items()}
    if blocked: score, status = None, blocked
    else:
        maximum = sum(stage_maximum(q, s) for s in q.stages)
        score = sum(s["points"] for s in stages.values()) / maximum
        status = _status(score, 1)
        if score == 0 and all(s["status"] == "A" for s in stages.values()): status = "A"
        elif score == 0 and any(s["status"] == "V" for s in stages.values()): status = "V"
    return {"question_id": q.id, "item_version": q.version, "repeat": repeat, "board": q.board,
            "category": q.category, "work": q.work, "score_policy": q.score_policy, "status": status, "item_score": score,
            "scoring_state": assessment_state({"stages": stages}),
            "format_compliant": all(s.get("format_compliant") for s in stages.values()),
            "dimension_scores": dimensions, "stages": stages,
            "summary_only": (stages["a"]["points"] / stage_maximum(q, "a") if q.board == "story" and stages["a"]["points"] is not None else None)}
