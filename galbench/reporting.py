"""Independent board aggregation, repeat statistics and reviewable artifacts."""
from __future__ import annotations

import csv
import copy
import json
import statistics
import math
import datetime as dt
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from .data import TEXT_WEIGHTS
from .identity import model_identity
from .scoring import DIMS, SCORER_VERSION, assessment_state
from .manual import has_execution_issue, has_manual_score, original_stage

SCORE_SCALE = 100


def rounded_score(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def timing_stats(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    if not values: return {"samples": 0, "mean_seconds": None, "median_seconds": None, "p95_seconds": None, "max_seconds": None}
    return {"samples": len(values), "mean_seconds": statistics.mean(values), "median_seconds": statistics.median(values),
            "p95_seconds": values[math.ceil(.95*len(values))-1], "max_seconds": values[-1]}


def prediction_seconds(prediction):
    return prediction.get("elapsed_seconds", (prediction.get("response") or {}).get("elapsed_seconds"))


def estimated_generation_wall(predictions):
    intervals = []
    for p in predictions:
        elapsed = prediction_seconds(p)
        if p.get("executed") is False or elapsed is None: continue
        try:
            start = dt.datetime.fromisoformat(p["created_at"]).timestamp()
            intervals.append((start, start+elapsed))
        except (ValueError, TypeError, KeyError): pass
    return max(end for _, end in intervals)-min(start for start, _ in intervals) if intervals else None


def percent_summary(summary):
    """Convert historical display values without rewriting source artifacts."""
    result = copy.deepcopy(summary)
    original = result.get("score_scale", 1000)
    if original not in {100, 1000}: raise ValueError("Unsupported score scale")
    fields = {"score_unrounded", "gal_score", "leaderboard_score", "summary_only_score",
              "work_macro_score", "repeat_score_stddev"}
    def convert(value):
        if not isinstance(value, dict): return
        for key, child in value.items():
            if key in fields and child is not None:
                value[key] = child * SCORE_SCALE / original
                if key not in {"score_unrounded", "repeat_score_stddev"}: value[key] = rounded_score(value[key])
            elif isinstance(child, dict): convert(child)
    if original != SCORE_SCALE: convert(result.get("boards", {}))
    result["score_scale"] = SCORE_SCALE
    if original != SCORE_SCALE: result["source_score_scale"] = original
    return result


def metrics(rows):
    n = len(rows)
    counts = Counter(r["status"] for r in rows)
    complete = bool(n) and not (counts["ERROR"] or counts["HOLD"])
    known = [r["item_score"] for r in rows if r["item_score"] is not None]
    responded = [r for r in rows if not has_execution_issue(r)]
    attempted = [r for r in responded if r["status"] in {"C", "P", "W"}]
    response_counts = Counter(r["status"] for r in responded)
    answered = len(responded)
    return {"samples": n, "questions": len({r["question_id"] for r in rows}), "complete": complete,
            "answered_samples": answered, "execution_failed_samples": n-answered,
            "counts": {s: counts[s] for s in ("C", "P", "W", "A", "V", "HOLD", "ERROR")},
            "mean_score": sum(known) / n if complete else None,
            "known_score_sum": sum(known), "pending_samples": counts["HOLD"] + counts["ERROR"],
            "scored_samples": len(known), "score_coverage": len(known)/n if n else 0,
            "manual_scored_samples": sum(has_manual_score(r) for r in rows),
            "full_correct_rate": response_counts["C"] / answered if answered else None,
            "partial_correct_rate": response_counts["P"] / answered if answered else None,
            "coverage": len(attempted) / answered if answered else None,
            "mean_given_attempted": statistics.mean(r["item_score"] for r in attempted) if attempted else None,
            "wrong_answer_rate": response_counts["W"] / answered if answered else None,
            "abstention_rate": response_counts["A"] / answered if answered else None,
            "invalid_output_rate": response_counts["V"] / answered if answered else None,
            "format_compliance_rate": sum(bool(r["format_compliant"]) for r in responded) / len(responded) if responded else None}


def result_state(rows, missing_categories=()):
    states = Counter(assessment_state(r) for r in rows)
    failures = states["execution_failed"] + states["not_called"]
    if failures: return "execution_failed" if failures == len(rows) else "execution_incomplete"
    if states["cancelled"]: return "stopped"
    if states["judge_failed"]: return "judge_failed"
    if states["needs_review"]: return "needs_review"
    if missing_categories: return "topic_only"
    if any(has_manual_score(r) for r in rows): return "scored_with_manual"
    return "scored"


def score_policy(board, rows):
    policies = {r.get("score_policy", "category_weighted" if board == "text" else "item_mean") for r in rows}
    if len(policies) > 1 or policies - {"category_weighted", "item_mean"}:
        raise ValueError("Mixed or invalid board score policies")
    return next(iter(policies), "category_weighted" if board == "text" else "item_mean")


def partial_score(board, rows):
    """Known-only reference scores plus honest bounds on the complete score."""
    known = [r["item_score"] for r in rows if r["item_score"] is not None]
    unavailable = []
    if board == "text" and score_policy(board, rows) == "category_weighted":
        lower = upper = reference = 0
        for category, weight in TEXT_WEIGHTS.items():
            group = [r for r in rows if r["category"] == category]
            values = [r["item_score"] for r in group if r["item_score"] is not None]
            if group:
                lower += weight * sum(values)/len(group)
                upper += weight * (sum(values)+len(group)-len(values))/len(group)
            else: upper += weight
            if values: reference += weight * statistics.mean(values)
            else: unavailable.append(category)
        if unavailable: reference = None
    else:
        lower = sum(known)/len(rows) if rows else 0
        upper = (sum(known)+len(rows)-len(known))/len(rows) if rows else 1
        reference = statistics.mean(known) if known else None
    return {"scored_samples": len(known), "score_coverage": len(known)/len(rows) if rows else 0,
            "manual_scored_samples": sum(has_manual_score(r) for r in rows),
            "provisional_score": rounded_score(100*reference) if reference is not None else None,
            "score_lower_bound": rounded_score(100*lower), "score_upper_bound": rounded_score(100*upper),
            "display_score": rounded_score(100*(reference if reference is not None else lower)),
            "display_score_kind": "known_only" if reference is not None else "confirmed_lower_bound",
            "unscored_categories": unavailable}


def board_score(board, rows):
    group = metrics(rows)
    categories = {cat: metrics([r for r in rows if r["category"] == cat]) for cat in sorted({r["category"] for r in rows})}
    policy = score_policy(board, rows)
    missing = sorted(set(TEXT_WEIGHTS) - set(categories)) if board == "text" and policy == "category_weighted" else []
    if not group["complete"] or missing: value = None
    elif board == "text" and policy == "category_weighted": value = SCORE_SCALE * sum(weight * categories[cat]["mean_score"] for cat, weight in TEXT_WEIGHTS.items())
    else: value = SCORE_SCALE * group["mean_score"]
    return {**group, **partial_score(board, rows), "result_state": result_state(rows, missing), "score_unrounded": value, "gal_score": rounded_score(value) if value is not None else None,
            "score_policy": policy, "missing_categories": missing, "by_category": categories}


def tokens(responses):
    total = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"), 0)
    total["responses_with_usage"] = sum(bool(r.get("usage")) for r in responses)
    total["responses_without_usage"] = sum(not r.get("usage") for r in responses)
    for response in responses:
        usage = response.get("usage") or {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(usage.get(field)) in (int, float): total[field] += usage[field]
        details = usage.get("completion_tokens_details") or {}
        if isinstance(details, dict) and type(details.get("reasoning_tokens")) in (int, float):
            total["reasoning_tokens"] += details["reasoning_tokens"]
    return total


def summarize(scores, predictions, manifest, questions, service):
    boards = {}
    for board in sorted({r["board"] for r in scores}):
        rows = [r for r in scores if r["board"] == board]
        group = board_score(board, rows)
        group["answer_timing"] = timing_stats([r.get("answer_elapsed_seconds") for r in rows if not has_execution_issue(r)])
        by_repeat = {str(i): board_score(board, [r for r in rows if r["repeat"] == i]) for i in range(manifest["repeats"])}
        vals = [g["score_unrounded"] for g in by_repeat.values()]
        group["by_repeat"] = by_repeat
        group["repeat_score_stddev"] = statistics.pstdev(vals) if len(vals) > 1 and all(v is not None for v in vals) else None
        group["by_work"] = {work: metrics([r for r in rows if r["work"] == work]) for work in sorted({r["work"] for r in rows})}
        works = [w["mean_score"] for w in group["by_work"].values()]
        group["work_macro_score"] = SCORE_SCALE * statistics.mean(works) if works and all(v is not None for v in works) else None
        selected = [q for q in questions if q.board == board]
        eligible = (group["gal_score"] is not None and not group["manual_scored_samples"] and manifest["mode"] == "api" and manifest["repeats"] >= 3
                    and all(q.private["review"]["eligible_for_leaderboard"] and not q.private["sample_only"]
                            and q.row["split"] == "test" for q in selected))
        group["leaderboard_eligible"] = eligible
        group["leaderboard_score"] = group["gal_score"] if eligible else None
        if board == "story":
            summaries = [r["summary_only"] for r in rows]
            group["summary_only_score"] = rounded_score(SCORE_SCALE * statistics.mean(summaries)) if all(v is not None for v in summaries) else None
            dimensions = DIMS[:4] if all(q.summary_only for q in selected) else DIMS
            group["task_mode"] = "summary_only" if all(q.summary_only for q in selected) else "legacy_summary_and_inference"
            group["dimension_means"] = {dim: statistics.mean(r["dimension_scores"][dim] for r in rows)
                                       if all(dim in r["dimension_scores"] for r in rows) else None for dim in dimensions}
        boards[board] = group
    status = "incomplete" if any(r["status"] == "ERROR" for r in scores) else "needs_review" if any(r["status"] == "HOLD" for r in scores) else "complete"
    successes = [p["response"] for p in predictions if p["state"] == "ok"]
    responses = [p["response"] for p in predictions if isinstance(p.get("response"), dict)]
    answer_timing = timing_stats([prediction_seconds(p) for p in predictions if p["state"] == "ok"])
    returned_models = sorted({value for r in successes for value in [r.get("returned_model"),*(r.get("returned_models") or [])]
                             if isinstance(value,str) and value.strip()})
    identity = model_identity({"model":manifest["model"]["name"], "returned_models":returned_models}, manifest["model"])
    return {"run_id": manifest["run_id"], "mode": manifest["mode"], "status": status,
            "scorer_version": SCORER_VERSION, "source_run": manifest.get("source_run"),
            "result_state": result_state(scores), "execution_abort": manifest.get("execution_abort"),
            "score_scale": SCORE_SCALE,
            "transport": manifest["model"].get("provider", "openai_compatible"),
            "judge_transport": (service.identity or {}).get("provider", "openai_compatible") if service.identity else None,
            "dataset_id": manifest.get("dataset_id"),
            "protocol": manifest.get("prompt_version", "three-boards-1.0"),
            "model": manifest["model"]["name"], "boards": boards,
            "model_identity": identity, "returned_models": returned_models,
            "returned_providers": sorted({r["returned_provider"] for r in successes if isinstance(r.get("returned_provider"), str) and r["returned_provider"].strip()}),
            "operations": {"planned_target_calls": sum(len(q.stages) for q in questions) * manifest["repeats"],
                "completed_target_calls": len(successes), "judge_calls_this_grading": service.calls,
                "initial_target_concurrency": manifest.get("concurrency"),
                "final_target_concurrency": manifest.get("target_final_concurrency", manifest.get("concurrency")),
                "issued_target_calls": sum(p.get("executed", True) for p in predictions),
                "skipped_target_calls": sum(p.get("executed") is False for p in predictions),
                "execution_failed_samples": sum(has_execution_issue(r) and assessment_state(r)!="cancelled" for r in scores),
                "cancelled_target_calls":sum(bool(p.get("cancelled")) for p in predictions),
                "manual_scored_samples": sum(has_manual_score(r) for r in scores),
                "manual_score_pending_samples": sum(r["item_score"] is None for r in scores),
                "judging_failed_samples": sum(assessment_state(r) == "judge_failed" for r in scores),
                "review_pending_samples": sum(assessment_state(r) == "needs_review" for r in scores),
                "truncated_target_calls": sum(r.get("finish_reason") == "length" for r in responses),
                "recovered_target_warning_calls": sum(any(not str(w).startswith("agent_image_capability_unadvertised：") for w in r.get("warnings",[])) for r in responses),
                "image_capability_advisory_calls": sum(any(str(w).startswith("agent_image_capability_unadvertised：") for w in r.get("warnings",[])) for r in responses),
                "truncated_judge_calls": sum(r.get("finish_reason") == "length" for r in service.responses),
                "judge_cache_hits": service.cache_hits,
                "judge_format_retries": service.format_retries,
                "judge_http_retries": sum(max(0, r.get("http_attempts", 1)-1) for r in service.responses) + sum(
                    max(0, (e.get("error_details") or {}).get("http_attempts", 1)-1) for e in service.errors if not e.get("replayed_offline")),
                "rules_scored_samples": sum(r["status"] not in {"ERROR", "HOLD"} and all(s.get("method") in {"rules", "extraction"} for s in r["stages"].values()) for r in scores),
                "generation_wall_seconds": manifest.get("generation_wall_seconds", estimated_generation_wall(predictions)),
                "generation_wall_estimated": "generation_wall_seconds" not in manifest,
                "answer_timing": answer_timing,
                "pending_reviews": sum(not r.get("resolved") for r in service.requests),
                "generation_usage": tokens(responses),
                "judge_usage_this_grading": tokens(service.responses),
                "mean_target_latency_seconds": answer_timing["mean_seconds"]},
            "notes": ["Three independent boards; no combined ranking score.",
                      "Sample data and mock scores are not official leaderboard results.",
                      "Subjective judgments require a judge or human annotations; unresolved items stay HOLD.",
                      "Incomplete boards show known-only reference scores and full-score bounds, not a final score.",
                      "Manual scores preserve original execution failures and are ineligible for official rankings.",
                      "Usage reflects returned fields; failed requests may have unreported costs."]}


def report_markdown(summary, scores):
    label = ("MOCK DEMO — fixture responses, not real model scores" if summary["mode"] == "mock_demo" else
             "Official agent CLI evaluation — separate from raw API results" if summary["mode"] == "agent_cli" else "API evaluation")
    lines = ["# galbench", "", f"**{label}**", "", f"Model: {summary['model']} · Status: {summary['status']}", "",
             f"Protocol: {summary.get('protocol', 'three-boards-1.0')}", "",
             "The three boards are independent. Sample results are not an official leaderboard.", "",
             f"| Board | Items × repeats | Score / {summary.get('score_scale', 1000)} | C | P | W | A | V | HOLD | ERROR |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for board, group in summary["boards"].items():
        c = group["counts"]
        value = group["gal_score"] if group["gal_score"] is not None else group.get("result_state", "UNSCORED").upper()
        lines.append(f"| {board} | {group['samples']} | {value} | " + " | ".join(str(c[s]) for s in ("C", "P", "W", "A", "V", "HOLD", "ERROR")) + " |")
    lines += ["", "Incomplete boards: reference scores use scored items only; ranges include all unresolved items.",
              "Manual scores preserve original errors and are not eligible for official rankings.", "",
              "| Board | Reference / 100 | Scored / total | Full-score range | Manually scored |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for board, group in summary["boards"].items():
        lines.append(f"| {board} | {group.get('provisional_score')} | {group.get('scored_samples')}/{group['samples']} | {group.get('score_lower_bound')}–{group.get('score_upper_bound')} | {group.get('manual_scored_samples',0)} |")
    if "story" in summary["boards"]:
        story = summary["boards"]["story"]
        lines += ["", f"Story SummaryOnly / {summary.get('score_scale', 1000)}: **{story['summary_only_score']}**", "",
                  "| Story dimension | Mean points |", "| --- | ---: |"]
        lines += [f"| {name} | {value} |" for name, value in story["dimension_means"].items()]
    lines += ["", "## Item details", "", "C=full credit; P=partial; W=wrong; A=abstained; V=unextractable; HOLD=review; ERROR=execution.", ""]
    for row in scores:
        lines += [f"### {row['question_id']} · repeat {row['repeat'] + 1} · {row['status']}", "",
                  f"Board: {row['board']} · Item score: {row['item_score']}", ""]
        for stage, outcome in row["stages"].items():
            body = {k: v for k, v in outcome.items() if k not in {"evidence_ids", "scorer_version"}}
            # Escape backticks so a model's output cannot terminate the report fence.
            dump = json.dumps(body, ensure_ascii=False, indent=2).replace("`", "\\u0060")
            lines += [f"Stage {stage}", "", "```json", dump, "```", ""]
    lines += ["## Operations", "", "```json", json.dumps(summary["operations"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def write_reports(output, summary, scores):
    (output / "report.md").write_text(report_markdown(summary, scores), encoding="utf-8")
    story = summary["boards"].get("story")
    dimensions = (DIMS[:4] if story["task_mode"] == "summary_only" else DIMS) if story else ()
    fields = ["question_id", "item_version", "repeat", "board", "category", "work", "status", "scoring_state", "manual_scored", "manual_score_reasons", "answer_elapsed_seconds", "execution_error", "judging_error", "score_percent", "item_score", "format_compliant", *dimensions]
    def safe(v):
        return "'" + v if isinstance(v, str) and v.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) else v
    with (output / "scores.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in scores:
            failures = {kind: "\n".join(original_stage(s).get("reason", "") for s in row["stages"].values()
                        if assessment_state(original_stage(s)) in kinds) for kind, kinds in {
                            "execution_error": {"execution_failed", "not_called"}, "judging_error": {"judge_failed"}}.items()}
            writer.writerow({k: safe(v) for k, v in {**row, **failures, "manual_scored":has_manual_score(row),
                "manual_score_reasons":"\n".join(s["manual_score"]["reason"] for s in row["stages"].values() if s.get("manual_score")),
                "score_percent": round(100 * row["item_score"], 4) if row["item_score"] is not None else None, **row["dimension_scores"]}.items()})
