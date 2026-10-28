"""Resumable generation, sealed story stages and independent grading."""
from __future__ import annotations
import datetime as dt
import json
import platform
import shutil
import time
import tomllib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from . import __version__
from .api import APIError, ModelConfig
from .clients import AGENTS, AgentClient, AgentConfig, create_client, execution_error, model_config
from .data import PROMPT_VERSION, SCHEMA_VERSION, fingerprint
from .judging import JUDGE_VERSION, JudgmentService
from .reporting import summarize, write_reports
from .scoring import SCORER_VERSION, assessment_state, combine, grade_stage, stage_maximum
from . import manual
from .control import RunCancelled, RunMonitor, call_scope

def timestamp(): return dt.datetime.now(dt.timezone.utc).isoformat()
def new_run_name(): return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

def concurrency_signal(prediction):
    transient = {"agent_quota_or_rate_limit", "agent_request_timeout", "agent_connection_error"}
    if prediction.get("state") == "error" and prediction.get("error_code") in transient:
        return prediction["error_code"], "execution_failure"
    if prediction.get("state") == "ok":
        for warning in prediction.get("response", {}).get("warnings", []):
            if isinstance(warning, str):
                error = execution_error(0, warning)
                if error.code in transient: return error.code, "recovered_warning"
    return None

def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)

def write_jsonl(path, rows):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows: stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    temp.replace(path)

def read_jsonl(path, repair_tail=False):
    if not path.exists(): return []
    raw = path.read_bytes(); lines = raw.splitlines(keepends=True); rows = []
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict): raise ValueError("Expected object")
            rows.append(row)
        except (ValueError, UnicodeError):
            if repair_tail and i == len(lines) - 1 and not line.endswith(b"\n"):
                path.write_bytes(b"".join(lines[:-1])); return rows
            raise ValueError(f"Invalid JSONL log at line {i + 1}") from None
    if repair_tail and raw and not raw.endswith(b"\n"):
        with path.open("ab") as stream: stream.write(b"\n")
    return rows

def load_config(path):
    with path.open("rb") as stream: config = tomllib.load(stream)
    if set(config) - {"model", "runtime", "judge"}: raise ValueError("Unknown configuration table")
    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict) or set(runtime) - {"dataset", "datasets", "output_dir", "boards", "repeats", "concurrency"}:
        raise ValueError("Unknown runtime setting")
    if "dataset" in runtime and "datasets" in runtime: raise ValueError("Use dataset or datasets, not both")
    return runtime, model_config(config["model"]), model_config(config["judge"]) if config.get("judge") else None

def latest_predictions(predictions, manifest):
    latest = {}
    for row in predictions:
        qid, repeat, stage = row.get("question_id"), row.get("repeat"), row.get("stage")
        if (row.get("evaluation_hash") != manifest["evaluation_hash"] or qid not in manifest["question_ids"]
                or type(repeat) is not int or not 0 <= repeat < manifest["repeats"]
                or stage not in manifest["stages"].get(qid, [])
                or row.get("state") not in {"ok", "error"}
                or (row["state"] == "ok" and not isinstance(row.get("response"), dict))):
            raise ValueError("Prediction log contains a task/protocol mismatch")
        key = (qid, repeat, stage)
        if key in latest and latest[key]["state"] == row["state"] == "ok":
            raise ValueError("Multiple successful generations for one task")
        latest[key] = row
    return latest

def load_judge_config(path):
    with path.open("rb") as stream: config = tomllib.load(stream)
    if set(config) != {"judge"}: raise ValueError("Judge config must contain only a [judge] table")
    return model_config(config["judge"])

def prepare_output(path):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Output is not empty: {path}; use --resume or a new directory")
    path.mkdir(parents=True, exist_ok=True)

def grade_predictions(dataset, manifest, predictions, output, judge_config=None, review_files=(), progress=print, *, judge_api_key=None,
                      concurrency=4, offline=False, manual_scores=(), cancel_token=None, monitor=None, on_event=None):
    if type(concurrency) is not int or not 1 <= concurrency <= 32: raise ValueError("concurrency must be in [1,32]")
    grading_started = time.perf_counter()
    monitor = monitor or RunMonitor(output,on_event)
    lookup = {q.id: q for q in dataset.questions}
    questions = [lookup[qid] for qid in manifest["question_ids"]]
    latest = latest_predictions(predictions, manifest)
    previous = {(r["question_id"],r["repeat"]):r for r in read_jsonl(output / "scores.jsonl")}
    manual_records = read_jsonl(output / "manual_scores.jsonl") + list(manual_scores)
    manual_map = {}
    for record in manual_records:
        manual.validate_submission(record)
        key = (record.get("question_id"), record.get("repeat"), record.get("stage"))
        if key[0] not in lookup or type(key[1]) is not int or not 0 <= key[1] < manifest["repeats"] or key[2] not in lookup[key[0]].stages:
            raise ValueError("人工评分题目、轮次或阶段不属于本次运行")
        manual_map[key] = record
    stale_manual = []
    submitted = read_jsonl(output / "manual_reviews.submitted.jsonl")
    submitted += [row for path in review_files for row in read_jsonl(Path(path))]
    # New annotations supersede old submissions for exactly the same rubric/response hash.
    review_map = {}
    for row in submitted:
        if row.get("observation") is None: continue
        if not isinstance(row.get("request_hash"), str): raise ValueError("Invalid manual review hash")
        review_map[row["request_hash"]] = row
    reviews = list(review_map.values())
    identity = manifest.get("judge") if offline and judge_config is None else judge_config.public_dict() if judge_config else None
    if offline and identity and identity.get("provider") in set(AGENTS) and manifest.get("judge_transport"):
        identity = {**identity, "runtime": manifest["judge_transport"]}
    may_recover = offline or bool(judge_config and manifest.get("judge") == judge_config.public_dict())
    service = JudgmentService(output, judge_config, cached=read_jsonl(output / "judgments.jsonl"), reviews=reviews, api_key=judge_api_key,
        allow_calls=not offline, cache_identity=identity if offline else None,
        cached_failures=read_jsonl(output / "judge_errors.jsonl") if may_recover else (),cancel_token=cancel_token,monitor=monitor)

    def grade_one(job, *, retry=False):
        q, repeat = job
        grading_event = {"question_id":q.id,"board":q.board,"repeat":repeat,"stage":"grading","started_at":timestamp()}
        monitor.emit({**grading_event,"type":"score_started"})
        outcomes = {}
        for stage in q.stages:
            prediction = latest.get((q.id, repeat, stage))
            record = manual_map.get((q.id, repeat, stage))
            expected = manual.context(q, repeat, stage, prediction, manifest["evaluation_hash"])
            if record and manual.matches(record, expected):
                original = previous.get((q.id, repeat), {}).get("stages", {}).get(stage)
                if original is None: raise ValueError("人工评分缺少原评分记录")
                outcomes[stage] = manual.apply(q, stage, original, record)
                continue
            if record: stale_manual.append({"question_id":q.id,"repeat":repeat,"stage":stage})
            if prediction is None or prediction["state"] != "ok":
                outcomes[stage] = {"stage": stage, "status": "ERROR", "points": None, "item_score": None,
                    "max_points": stage_maximum(q, stage), "format_compliant": False,
                    "reason": prediction.get("error", "generation_not_completed") if prediction else "generation_not_completed", "method": "execution"}
            else:
                outcomes[stage] = grade_stage(q, stage, prediction["response"], service, repeat)
            if prediction:
                outcomes[stage]["elapsed_seconds"] = prediction.get("elapsed_seconds", prediction.get("response", {}).get("elapsed_seconds"))
        row = combine(q, repeat, outcomes)
        durations = [s["elapsed_seconds"] for s in outcomes.values() if s.get("elapsed_seconds") is not None]
        row["answer_elapsed_seconds"] = sum(durations) if durations else None
        progress(f"{'retry_score' if retry else 'score'} {q.id} repeat={repeat + 1} {row['status']}")
        monitor.emit({**grading_event,"type":"score_finished"})
        return row

    jobs = [(q, r) for q in questions for r in range(manifest["repeats"])]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        scores = list(executor.map(grade_one, jobs))
    # Retry temporary transport failures once after the main grading load has
    # subsided. Format failures, refusal, truncation and auth errors stay blocked.
    retry_indices = [i for i, row in enumerate(scores) if assessment_state(row) == "judge_failed" and any(
        request.get("question_id") == row["question_id"] and request.get("request_hash") in service.retryable_requests
        for request in service.requests)] if service.client and service.transport_recovery_enabled else []
    recovered = 0
    if retry_indices and not (cancel_token and cancel_token.event.is_set()):
        progress(f"grading_retry: {len(retry_indices)} temporary failure(s), concurrency={min(2, concurrency)}")
        with ThreadPoolExecutor(max_workers=min(2, concurrency)) as executor:
            retried = list(executor.map(lambda job: grade_one(job, retry=True), [jobs[i] for i in retry_indices]))
        for i, row in zip(retry_indices, retried):
            scores[i] = row
            recovered += assessment_state(row) == "scored"
    summary = summarize(scores, list(latest.values()), manifest, questions, service)
    summary["operations"]["judge_retry_samples"] = len(retry_indices)
    summary["operations"]["judge_retry_recovered_samples"] = recovered
    summary["operations"]["stale_manual_scores"] = len({(r["question_id"],r["repeat"],r["stage"]) for r in stale_manual})
    summary["aggregation_version"] = "fixed-answer-and-partial-2.0"
    summary["operations"]["grading_wall_seconds"] = time.perf_counter() - grading_started
    summary["operations"]["grading_concurrency"] = concurrency
    summary["scored_at"] = timestamp()
    summary["operations"]["runtime_issues"] = monitor.snapshot()
    if cancel_token and cancel_token.event.is_set():
        manifest.update(stopped=True,stop_requested_at=cancel_token.requested_at,stopped_at=timestamp())
    if manifest.get("stopped"):
        summary.update(stopped=True,status="stopped",result_state="stopped",stop_requested_at=manifest.get("stop_requested_at"))
        for group in summary["boards"].values():
            group.update(leaderboard_eligible=False,leaderboard_score=None)
    write_jsonl(output / "scores.jsonl", scores)
    write_json(output / "summary.json", summary)
    write_jsonl(output / "review_requests.jsonl", [r for r in service.requests if not r.get("resolved")])
    write_jsonl(output / "judge_errors.jsonl", service.errors)
    write_jsonl(output / "manual_reviews.submitted.jsonl", reviews)
    if manual_records: write_jsonl(output / "manual_scores.jsonl", manual_records)
    write_reports(output, summary, scores)
    if not offline: manifest["judge"] = judge_config.public_dict() if judge_config else None
    if isinstance(judge_config, AgentConfig) and service.client: manifest["judge_transport"] = service.client.info()
    manifest["offline_grading"] = offline
    manifest["judge_version"] = JUDGE_VERSION
    manifest["judge_transport_policy"] = {"http_max_retries": getattr(getattr(service.client, "config", None), "max_retries", None),
        "tail_retry_passes": 1 if service.transport_recovery_enabled else 0, "tail_retry_concurrency": min(2, concurrency)}
    manifest["scorer_version"] = SCORER_VERSION
    manifest["grading_galbench_version"] = __version__
    manifest["grading_source_sha256"] = fingerprint({p.name: p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("*.py")})
    manifest["status"] = summary["status"]; manifest["updated_at"] = timestamp()
    write_json(output / "manifest.json", manifest)
    return summary

def run(dataset, model, output, *, judge_config=None, boards=("text", "vision", "story"),
        repeats=1, concurrency=3, limit=None, resume=False, mode="agent_cli", review_files=(), progress=print,
        target_api_key=None, judge_api_key=None, on_event=None, cancel_token=None):
    if type(repeats) is not int or not 1 <= repeats <= 100: raise ValueError("repeats must be in [1,100]")
    if type(concurrency) is not int or not 1 <= concurrency <= 32: raise ValueError("concurrency must be in [1,32]")
    if limit is not None and (type(limit) is not int or limit < 1): raise ValueError("limit must be positive")
    if not boards or set(boards) - {"text", "vision", "story"}: raise ValueError("Invalid boards")
    questions = [q for q in dataset.questions if q.board in boards and q.row["split"] != "retired"]
    if limit is not None: questions = questions[:limit]
    if not questions: raise ValueError("No questions selected")
    if isinstance(model, ModelConfig): mode = "mock_demo"
    elif not isinstance(model, AgentConfig): raise ValueError("真实模型必须通过 Agent Harness 接入")
    if any(q.board == "vision" for q in questions) and not model.supports_vision:
        raise ValueError("Vision selected but supports_vision=false; use --board text/--board story")
    with call_scope(cancel_token):
        client = create_client(model, api_key=target_api_key)
        if judge_config: create_client(judge_config, api_key=judge_api_key)
        startup_error = None
        if isinstance(client, AgentClient):
            try:client.check_runtime(require_images=any(q.board=="vision" for q in questions))
            except APIError as exc:startup_error=exc
    if isinstance(model, AgentConfig) and mode != "mock_demo": mode = "agent_cli"
    output = output.resolve()
    settings = {"dataset_sha256": dataset.sha256, "dataset_input_hash": dataset.input_hash,
                "model": model.public_dict(), "prompt_version": PROMPT_VERSION if any(q.row["schema_version"] == SCHEMA_VERSION for q in questions) else "three-boards-1.0", "repeats": repeats,
                "question_ids": [q.id for q in questions], "stages": {q.id: list(q.stages) for q in questions}, "mode": mode}
    if isinstance(model, AgentConfig): settings["target_transport"] = client.info()
    evaluation_hash = fingerprint(settings)
    if resume:
        if not (output / "manifest.json").exists(): raise ValueError("Cannot resume: missing manifest")
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if manifest["evaluation_hash"] != evaluation_hash: raise ValueError("Resume refused: dataset/model/protocol changed")
    else:
        prepare_output(output)
        manifest = {**settings, "evaluation_hash": evaluation_hash, "run_id": output.name,
            "dataset_id": dataset.paths[0].parent.name,
            "created_at": timestamp(), "status": "running", "concurrency": concurrency,
            "dataset_paths": [str(p) for p in dataset.paths], "galbench_version": __version__,
            "python_version": platform.python_version(),
            "source_sha256": fingerprint({p.relative_to(Path(__file__).parent).as_posix(): p.read_text(encoding="utf-8") for p in Path(__file__).parent.rglob("*.py")}),
            "question_inputs": {q.id: q.public_signature for q in questions},
            "question_versions": {q.id: q.version for q in questions}}
        write_json(output / "manifest.json", manifest)
    log = output / "predictions.jsonl"
    predictions = read_jsonl(log, repair_tail=True)
    latest = latest_predictions(predictions, manifest)
    for field in ("stopped","stopped_at","stop_requested_at"): manifest.pop(field,None)
    monitor = RunMonitor(output,on_event)

    def record_for(q, repeat, stage):
        return {"question_id": q.id, "item_version": q.version, "repeat": repeat, "stage": stage,
                  "evaluation_hash": evaluation_hash, "created_at": timestamp(),
                  "request_messages": q.messages(stage, redact=True)}

    def generate(q, repeat, stage, admitted_concurrency):
        started = time.perf_counter()
        record = {**record_for(q, repeat, stage), "executed": False, "dispatch_concurrency": admitted_concurrency}
        event = {"question_id": q.id, "repeat": repeat, "stage": stage, "board": q.board, "started_at": record["created_at"]}
        if on_event: on_event({**event, "type": "target_started"})
        try:
            context = {"question_id":q.id,"board":q.board,"repeat":repeat,"stage":stage}
            with call_scope(cancel_token,monitor.retry("target",**context)):
                if startup_error is not None:raise startup_error
                record["executed"] = True
                response = client.complete(q.messages(stage))
            if response.get("finish_reason") == "length":
                row = {**record, "state": "error", "error": "output_limit_reached", "response": response}
            else: row = {**record, "state": "ok", "response": response}
        except RunCancelled:
            row = {**record,"state":"error","cancelled":True,"error_code":"user_stopped","error":"run_stopped_by_user"}
        except (APIError, OSError, ValueError) as exc:
            row = {**record, "state": "error", "error": str(exc),
                    "error_code": getattr(exc, "code", None), "fatal_error": getattr(exc, "fatal", False),
                    "error_details": getattr(exc, "details", None)}
            available = (getattr(exc, "details", None) or {}).get("available_answer")
            if isinstance(available, str) and available:
                row["response"] = {"text":available,"execution_state":"error","finish_reason":None,"usage":{}}
        row["elapsed_seconds"] = time.perf_counter() - started
        if row["state"]=="error" and not row.get("cancelled"):
            monitor.issue("target",row.get("error_code") or ("output_limit_reached" if row.get("error")=="output_limit_reached" else "call_failed"),**context)
        for warning in row.get("response",{}).get("warnings",[]):
            # Capability metadata is advisory, not a failed/retried call.
            if isinstance(warning,str) and warning.startswith("agent_image_capability_unadvertised："):continue
            code = execution_error(0,warning).code if isinstance(warning,str) else None
            monitor.issue("target",code or "agent_reconnect_warning",kind="warning",**context)
        if on_event: on_event({**event, "type": "target_finished", "state":"cancelled" if row.get("cancelled") else row["state"], "elapsed_seconds": row["elapsed_seconds"]})
        return row

    # Persist summaries before issuing inference. No previous answer is supplied to a new request.
    generation_started = time.perf_counter()
    dispatch_limit = concurrency
    manifest["concurrency_adjustments"] = []
    manifest["target_started_at"] = timestamp()
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        abort = None
        manifest.pop("execution_abort", None)
        phases = (("answer", "a"),) + ((("b",),) if any("b" in q.stages for q in questions) else ())
        for phase in phases:
            jobs = [(q, r, stage) for q in questions for r in range(repeats) for stage in q.stages
                    if stage in phase and latest.get((q.id, r, stage), {}).get("state") != "ok"
                    and (stage != "b" or latest.get((q.id, r, "a"), {}).get("state") == "ok")]
            progress(f"phase {','.join(phase)}: {len(jobs)} target call(s) pending")
            processed = 0
            def persist(row):
                nonlocal processed
                processed += 1
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"); stream.flush()
                predictions.append(row); latest[row["question_id"], row["repeat"], row["stage"]] = row
                progress(f"[{processed}/{len(jobs)}] {row['question_id']} {row['stage']} repeat={row['repeat'] + 1} {row['state']}"
                         + (" skipped" if row.get("executed") is False else "") + (" cancelled" if row.get("cancelled") else ""))
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                remaining = iter(jobs)
                pending = set()
                def fill():
                    while not abort and not (cancel_token and cancel_token.event.is_set()) and len(pending) < dispatch_limit:
                        job = next(remaining, None)
                        if job is None: break
                        pending.add(executor.submit(generate, *job, dispatch_limit))
                fill()
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        row = future.result(); persist(row)
                        signal = concurrency_signal(row) if isinstance(model, AgentConfig) else None
                        # Requests already in flight under the old limit must not
                        # repeatedly penalize a limit that has just been lowered.
                        if signal and row["dispatch_concurrency"] == dispatch_limit and dispatch_limit > 1:
                            previous_limit = dispatch_limit
                            dispatch_limit = max(1, dispatch_limit // 2)
                            manifest["concurrency_adjustments"].append({"at":timestamp(),"limit":dispatch_limit,
                                "from_limit":previous_limit,"reason":signal[0],"trigger":signal[1],"question_id":row["question_id"]})
                            progress(f"concurrency: CLI {signal[1]}; subsequent calls limited to {dispatch_limit}")
                        if row.get("fatal_error") and abort is None:
                            abort = {"error_code": row.get("error_code"), "reason": row["error"]}
                            manifest["execution_abort"] = abort
                            progress("execution stopped: " + row["error"])
                    fill()
                if cancel_token and cancel_token.event.is_set():
                    for q,r,stage in remaining:
                        persist({**record_for(q,r,stage),"state":"error","executed":False,"cancelled":True,
                                 "error_code":"user_stopped","error":"run_stopped_by_user"})
                elif abort:
                    for q, r, stage in remaining:
                        persist({**record_for(q, r, stage), "state": "error", "executed": False,
                                 "error_code": "batch_aborted", "error": "batch_aborted：配置错误，未发出本题请求。\n" + abort["reason"]})
    manifest["generation_wall_seconds"] = time.perf_counter() - generation_started
    manifest["target_finished_at"] = timestamp()
    manifest["target_final_concurrency"] = dispatch_limit
    if on_event: on_event({"type": "target_phase_completed", "elapsed_seconds": manifest["generation_wall_seconds"]})
    progress("grading: checking answers and semantic observations")
    return grade_predictions(dataset, manifest, predictions, output, judge_config, review_files, progress,
                             judge_api_key=judge_api_key, concurrency=concurrency,cancel_token=cancel_token,monitor=monitor)

def rescore(source, dataset, output, judge_config=None, review_files=(), progress=print, *, offline=False, concurrency=4, judge_api_key=None, manual_scores=()):
    if offline and judge_config is not None: raise ValueError("Offline regrading uses the original judge identity; omit --judge-config")
    source, output = source.resolve(), output.resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    lookup = {q.id: q for q in dataset.questions}
    for qid in manifest["question_ids"]:
        if qid not in lookup or lookup[qid].public_signature != manifest["question_inputs"][qid]:
            raise ValueError(f"Cannot rescore: public question/image changed for {qid}")
    if judge_config and not offline: create_client(judge_config, api_key=judge_api_key)
    predictions = read_jsonl(source / "predictions.jsonl")
    latest_predictions(predictions, manifest)
    prepare_output(output)
    shutil.copyfile(source / "predictions.jsonl", output / "predictions.jsonl")
    for name in ("scores.jsonl", "manual_scores.jsonl", "runtime_events.jsonl"):
        if (source / name).exists(): shutil.copyfile(source / name, output / name)
    if (source / "judgments.jsonl").exists(): shutil.copyfile(source / "judgments.jsonl", output / "judgments.jsonl")
    if (source / "judge_errors.jsonl").exists(): shutil.copyfile(source / "judge_errors.jsonl", output / "judge_errors.jsonl")
    if (source / "manual_reviews.submitted.jsonl").exists():
        shutil.copyfile(source / "manual_reviews.submitted.jsonl", output / "manual_reviews.submitted.jsonl")
    manifest = {**manifest, "run_id": output.name, "source_run": str(source),
                "rescored_at": timestamp(), "grading_dataset_sha256": dataset.sha256,
                "grading_question_versions": {qid: lookup[qid].version for qid in manifest["question_ids"]}}
    return grade_predictions(dataset, manifest, predictions, output, judge_config, review_files, progress,
                             offline=offline, concurrency=concurrency, judge_api_key=judge_api_key, manual_scores=manual_scores)
