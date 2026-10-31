"""Loopback-only, dependency-free browser UI for the existing evaluator."""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import APIError, ModelConfig, DEFAULT_MAX_TOKENS
from .clients import AGENTS, CODEX_MODELS, AgentConfig, AgentClient, create_client, discover_agents
from .data import fingerprint, load_dataset, digest, dataset_label, dataset_coverage
from .demo import mock_server
from .runner import new_run_name, read_jsonl, rescore, run, timestamp, write_json
from .reporting import estimated_generation_wall, partial_score, percent_summary, prediction_seconds, result_state, timing_stats
from .scoring import SCORER_VERSION, assessment_state, grading_route
from . import manual
from . import arena
from .control import Cancellation, RunCancelled

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).with_name("static")
BOARDS = ("text", "vision", "story")
MAX_WEB_CONCURRENCY = 16
FILES = {"report.md", "summary.json", "scores.csv", "scores.jsonl", "predictions.jsonl",
         "review_requests.jsonl", "judge_errors.jsonl", "manifest.json", "manual_scores.jsonl", "runtime_events.jsonl"}
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low}–{high} 的整数")
    return value


def model_settings(value, label):
    if not isinstance(value, dict): raise ValueError(f"请填写{label}的 Agent 配置")
    provider = value.get("provider")
    if provider not in AGENTS: raise ValueError("直接 API 接口已移除，请选择 Agent Harness")
    if set(value)-{"provider","executable","cli_model","supports_vision","use_client_config","cli_profile","model_label","credential_env","timeout_seconds"}:
        raise ValueError("不再接收直接 API 配置或密钥；请在 Agent 客户端配置提供商和凭据")
    fields = {key:value[key] for key in ("executable","cli_model","supports_vision","use_client_config",
        "cli_profile","model_label","credential_env") if key in value}
    fields["timeout_seconds"] = integer(value.get("timeout_seconds",600),"超时秒数",10,1800)
    config = AgentConfig(provider,**fields)
    try: create_client(config)  # Version/help and command construction only, no model request.
    except APIError as exc: raise ValueError(str(exc)) from None
    return config,None


class BusyError(ValueError):
    pass


class WebApp:
    def __init__(self, dataset_path=None, runs_root=None):
        if dataset_path:
            self.datasets = {"custom": load_dataset(dataset_path)}
            self.default_dataset = "custom"
        else:
            self.datasets = {"expanded": load_dataset(ROOT / "data" / "dev-v2.0"),
                             "smoke": load_dataset(ROOT / "spec" / "v2.0")}
            self.default_dataset = "expanded"
        self.dataset = self.datasets[self.default_dataset]
        self.runs_root = Path(runs_root or ROOT / "runs").resolve()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.jobs = {}
        self.controls = {}

    def bootstrap(self):
        def description(key, dataset):
            questions = [q for q in dataset.questions if q.row["split"] != "retired"]
            return {"id": key, "label": dataset_label(key, dataset), "coverage": dataset_coverage(dataset),
                    "boards": {b: {"questions": sum(q.board == b for q in questions),
                        "rules_questions": sum(q.board == b and grading_route(q) == "rules" for q in questions),
                        "semantic_questions": sum(q.board == b and grading_route(q) != "rules" for q in questions),
                        "calls": sum(len(q.stages) for q in questions if q.board == b)} for b in BOARDS}}
        datasets = [description(key, dataset) for key, dataset in self.datasets.items()]
        with self.lock:
            active = next((j["id"] for j in self.jobs.values() if j["state"] in {"running","stopping"}), None)
        return {"token": self.token, "agents": discover_agents(), "codex_models": CODEX_MODELS, "active_run": active,
                "boards": datasets[0]["boards"], "datasets": datasets, "default_dataset": self.default_dataset,
                "dataset_label": datasets[0]["label"], "runs_root": str(self.runs_root),
                "max_concurrency": MAX_WEB_CONCURRENCY, "scorer_version": SCORER_VERSION}

    def start(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象")
        mode = payload.get("mode", "agent_cli")
        if mode not in {"agent_cli", "mock_demo"}:
            raise ValueError("不支持的测试模式")
        profile = payload.get("dataset_profile", self.default_dataset)
        if not isinstance(profile, str) or profile not in self.datasets:
            raise ValueError("不支持的题库，请刷新页面后选择")
        dataset = self.datasets[profile]
        boards = payload.get("boards", list(BOARDS))
        if (not isinstance(boards, list) or not boards or
                any(not isinstance(b, str) or b not in BOARDS for b in boards) or len(set(boards)) != len(boards)):
            raise ValueError("请至少选择一个榜单，且不要重复选择")
        repeats = integer(payload.get("repeats", 1), "重复轮数", 1, 10)
        concurrency = integer(payload.get("concurrency", 2), "并发数", 1, MAX_WEB_CONCURRENCY)
        judge_enabled = payload.get("judge_enabled", True)
        if type(judge_enabled) is not bool:
            raise ValueError("裁判开关须为布尔值")
        questions = [q for q in dataset.questions if q.board in boards and q.row["split"] != "retired"]
        judge_enabled = judge_enabled and any(grading_route(q) != "rules" for q in questions)
        model = judge = None
        target_key = judge_key = None
        if mode == "agent_cli":
            model, target_key = model_settings(payload.get("target"), "答题模型")
            if "vision" in boards and not model.supports_vision:
                raise ValueError("已选择识图榜，请使用支持图片的答题模型，或取消识图榜")
            if judge_enabled:
                settings = payload.get("judge")
                judge, judge_key = model_settings(settings, "裁判模型")
        questions = [q for q in dataset.questions if q.board in boards and q.row["split"] != "retired"]
        with self.lock:
            if any(j["state"] in {"running","stopping"} for j in self.jobs.values()):
                raise BusyError("已有测试正在运行，请等待当前测试结束")
            job_id = "web-" + new_run_name()
            self.jobs[job_id] = {"id": job_id, "state": "running", "mode": mode, "created_at": timestamp(),
                "dataset_profile": profile, "phase": "准备中", "logs": [], "target_done": 0, "scored": 0,
                "target_total": sum(len(q.stages) for q in questions) * repeats,
                "score_total": len(questions) * repeats, "error": None,
                "board_progress": {b:{"target_done":0,"target_total":sum(len(q.stages) for q in questions if q.board==b)*repeats,
                    "scored":0,"score_total":sum(q.board==b for q in questions)*repeats} for b in boards},
                "active_calls": {}, "active_grades":{},"recent_calls": [], "generation_wall_seconds": None,"stop_requested":False,
                "issues":{"total":0,"target":0,"judge":0,"warnings":0,"system":0,"recent":[]}}
            self.controls[job_id] = Cancellation()
            # Credentials live only in this worker's arguments, never in job state or saved configuration.
            worker = threading.Thread(target=self._work,
                args=(job_id, mode, model, judge, boards, repeats, concurrency, judge_enabled, dataset, profile),
                daemon=True, name="galbench-" + job_id)
            worker.start()
        return {"id": job_id, "cli_models": {"target": model.cli_model if isinstance(model, AgentConfig) else None,
                                             "judge": judge.cli_model if isinstance(judge, AgentConfig) else None}}

    def stop(self, payload):
        if not isinstance(payload,dict) or not isinstance(payload.get("run_id"),str):
            raise ValueError("请指定要停止的测试")
        job_id = payload["run_id"]
        with self.lock:
            if job_id not in self.jobs: raise FileNotFoundError("找不到此运行")
            job = self.jobs[job_id]
            if job["state"] not in {"running","stopping"}: return {"id":job_id,"state":job["state"]}
            first = not job["stop_requested"]
            job.update(stop_requested=True,state="stopping",phase="正在停止并保存")
            if first: job["logs"].append("stop: 用户要求停止测试；取消后续请求，保存已完成记录。")
            control = self.controls[job_id]
        control.cancel()
        return {"id":job_id,"state":"stopping"}

    def _work(self, job_id, mode, model, judge, boards, repeats, concurrency, judge_enabled, dataset, profile):
        question_boards = {q.id:q.board for q in dataset.questions}
        # Public display context only; no character names, reference answers or private scoring data.
        question_titles = {q.id:q.private["work"]["title"] for q in dataset.questions}
        def event(value):
            with self.lock:
                job = self.jobs[job_id]
                if value["type"] == "runtime_issue":
                    job["issues"] = {**value["issues"],"recent":[dict(r) for r in value["issues"]["recent"]]}
                    return
                if value["type"] == "target_phase_completed":
                    job["generation_wall_seconds"] = value["elapsed_seconds"]
                    return
                key = f"{value['question_id']}:{value['repeat']}:{value['stage']}"
                display_event = {**value, "board":question_boards.get(value["question_id"]),
                                 "work_title":question_titles.get(value["question_id"])}
                if value["type"] == "target_started": job["active_calls"][key] = display_event
                elif value["type"] == "score_started": job["active_grades"][key] = display_event
                elif value["type"] == "score_finished": job["active_grades"].pop(key,None)
                elif value["type"] == "target_finished":
                    job["active_calls"].pop(key, None)
                    job["recent_calls"] = [display_event, *job["recent_calls"]][:8]
        def progress(line):
            with self.lock:
                job = self.jobs[job_id]
                job["logs"].append(str(line))
                if line.startswith("phase "):
                    if not job["stop_requested"]: job["phase"] = "剧情后续推测" if line.startswith("phase b:") else "模型答题"
                elif line.startswith("[") and " cancelled" not in line:
                    job["target_done"] += 1
                    board = question_boards.get(line.split()[1])
                    if board in job["board_progress"]: job["board_progress"][board]["target_done"] += 1
                    if not job["stop_requested"]: job["phase"] = "模型答题"
                elif line.startswith("score "):
                    job["scored"] += 1
                    board = question_boards.get(line.split()[1])
                    if board in job["board_progress"]: job["board_progress"][board]["scored"] += 1
                    if not job["stop_requested"]: job["phase"] = "自动判分"
                elif line.startswith("grading:"):
                    if not job["stop_requested"]: job["phase"] = "自动判分"
                elif line.startswith("grading_retry:"):
                    if not job["stop_requested"]: job["phase"] = "正在低并发补判临时网络失败"
        try:
            kwargs = dict(boards=tuple(boards), repeats=repeats, concurrency=concurrency, progress=progress, on_event=event,cancel_token=self.controls[job_id])
            output = self.runs_root / job_id
            if mode == "mock_demo":
                with mock_server(dataset=dataset if profile in {"expanded", "smoke"} else None) as (url, _):
                    model = ModelConfig(url, "galbench-mock-target", api_key_env=None, supports_vision=True, timeout_seconds=10)
                    judge = ModelConfig(url, "galbench-mock-judge", api_key_env=None, timeout_seconds=10,
                                        parameters={"max_tokens": DEFAULT_MAX_TOKENS}) if judge_enabled else None
                    summary = run(dataset, model, output, judge_config=judge, mode="mock_demo", **kwargs)
            else:
                summary = run(dataset, model, output, judge_config=judge, **kwargs)
            with self.lock:
                self.jobs[job_id].update(state="stopped" if summary.get("stopped") else "finished",
                    phase="已停止" if summary.get("stopped") else "已完成", result_status=summary["status"],active_calls={},active_grades={})
        except RunCancelled:
            with self.lock:
                self.jobs[job_id].update(state="stopped",phase="准备阶段已停止",result_status="stopped",active_calls={})
        except Exception:
            # Do not expose exception values that may originate from user-supplied credentials.
            with self.lock:
                issues = self.jobs[job_id]["issues"]
                issues.update(total=issues["total"]+1,system=issues["system"]+1)
                issues["recent"] = [{"sequence":issues["total"],"source":"system","kind":"error","code":"runtime_failure",
                    "message":"本地运行异常，请展开比赛详情","question_id":None,"board":None,"repeat":0,"stage":None},*issues["recent"]][:8]
                self.jobs[job_id].update(state="failed", phase="执行失败",
                    error="测试未能完成。已生成的回答保留在运行目录；请检查服务地址、网络和本地文件权限。")

    def snapshot(self, job_id, after=0):
        with self.lock:
            if job_id not in self.jobs:
                raise FileNotFoundError("找不到此运行")
            job = self.jobs[job_id]
            result = {k: v for k, v in job.items() if k != "logs"}
            result["active_calls"] = [dict(v) for v in job["active_calls"].values()]
            result["active_grades"] = [dict(v) for v in job["active_grades"].values()]
            result["recent_calls"] = [dict(v) for v in job["recent_calls"]]
            result["board_progress"] = {k:dict(v) for k,v in job["board_progress"].items()}
            result["issues"] = {**job["issues"],"recent":[dict(r) for r in job["issues"]["recent"]]}
            result.update(logs=job["logs"][max(after, 0):], cursor=len(job["logs"]))
        if result["state"] == "finished" or (result["state"]=="stopped" and (self.runs_root/job_id/"summary.json").exists()):
            result["result"] = self.result(job_id)
        return result

    def folder(self, name):
        if not SAFE_NAME.fullmatch(name) or name in {".", ".."}:
            raise FileNotFoundError("找不到此运行")
        path = (self.runs_root / name).resolve()
        if path.parent != self.runs_root or not path.is_dir():
            raise FileNotFoundError("找不到此运行")
        return path

    def history(self):
        return arena.catalog(self.runs_root,self.datasets)[0]

    def arena_state(self):
        path = self.runs_root/".arena-state.json"
        if not path.exists(): return {"season":1,"hidden_roots":[],"cleared_at":None}
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value,dict) or type(value.get("season")) is not int or value["season"]<1
            or not isinstance(value.get("hidden_roots"),list) or any(not isinstance(v,str) or not SAFE_NAME.fullmatch(v) for v in value["hidden_roots"])):
            raise ValueError("排行榜设置无效；原始评测存档保持不变")
        return value

    def arena(self):
        records,profiles,skipped = arena.catalog(self.runs_root,self.datasets)
        with self.lock: state = self.arena_state()
        hidden = set(state["hidden_roots"])
        for row in records: row["excluded_from_season"] = row["root_run_id"] in hidden
        return {"records":records,"profiles":profiles,"entries":arena.standings(records,hidden),
                "season":state["season"],"cleared_at":state.get("cleared_at"),"can_restore":bool(hidden),"skipped_archives":skipped}

    def reset_arena(self,payload):
        if not isinstance(payload,dict) or payload.get("action") not in {"clear","restore"}: raise ValueError("未知榜单操作")
        with self.lock:
            state = self.arena_state()
            if payload["action"] == "clear":
                roots = {r["root_run_id"] for r in self.history()}
                state = {"season":state["season"]+1,"hidden_roots":sorted(set(state["hidden_roots"])|roots),"cleared_at":timestamp()}
            else: state = {"season":state["season"],"hidden_roots":[],"cleared_at":None}
            self.runs_root.mkdir(parents=True,exist_ok=True)
            write_json(self.runs_root/".arena-state.json",state)
        return self.arena()

    def showcase(self):
        dataset = self.datasets.get("expanded", self.datasets.get("smoke",self.dataset))
        candidates = [q for q in dataset.questions if q.board == "vision" and q.images]
        questions, works = [], set()
        preferred = {"CLANNAD":"gbd-v-002", "Kanon":"gbd-v-016", "ATRI -My Dear Moments-":"gbd-v-046"}
        for title, identifier in preferred.items():
            match = next((q for q in candidates if q.id == identifier and q.private["work"]["title"] == title), None)
            if match: questions.append(match); works.add(title)
        for q in candidates:
            if len(questions) == 3: break
            if q.private["work"]["title"] not in works:
                questions.append(q); works.add(q.private["work"]["title"])
        for q in candidates:
            if len(questions) == 3: break
            if q not in questions: questions.append(q)
        return {"portraits":[{"image":q.images[0].part()["image_url"]["url"],"work_title":q.private["work"]["title"]} for q in questions]}

    def result(self, name):
        folder = self.folder(name)
        summary = percent_summary(json.loads((folder / "summary.json").read_text(encoding="utf-8")))
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        summary["model_identity"] = arena.model_identity(summary, manifest.get("model",{}))
        summary.setdefault("protocol", manifest.get("prompt_version", "three-boards-1.0"))
        grading_hash = manifest.get("grading_dataset_sha256", manifest.get("dataset_sha256"))
        view_dataset = self.dataset
        dataset_matches = grading_hash == view_dataset.sha256
        for candidate in self.datasets.values():
            if grading_hash == candidate.sha256:
                view_dataset, dataset_matches = candidate, True
                break
        if not dataset_matches:
            # Only try a known bundled archive, never arbitrary paths from an old manifest.
            for bundled in (ROOT / "data" / "dev-v1.2", ROOT / "spec" / "v1.1", ROOT / "spec" / "v1"):
                try:
                    legacy = load_dataset(bundled)
                    if grading_hash == legacy.sha256:
                        view_dataset, dataset_matches = legacy, True
                        break
                except (OSError, ValueError):
                    continue
        scores = read_jsonl(folder / "scores.jsonl")
        # Also distinguish failures when viewing older archives, without rewriting them.
        summary["result_state"] = "stopped" if summary.get("stopped") else result_state(scores)
        for board, group in summary["boards"].items():
            board_rows = [r for r in scores if r["board"] == board]
            group["result_state"] = result_state(board_rows, group.get("missing_categories", []))
            group.update(partial_score(board, board_rows))
        latest = {(r["question_id"], r["repeat"], r["stage"]): r for r in read_jsonl(folder / "predictions.jsonl")}
        summary["operations"].setdefault("truncated_target_calls", sum(r.get("response", {}).get("finish_reason") == "length" for r in latest.values()))
        summary["operations"].setdefault("truncated_judge_calls", sum((r.get("response") or {}).get("finish_reason") == "length" for r in read_jsonl(folder / "judge_errors.jsonl")))
        lookup = {q.id: q for q in view_dataset.questions}
        if not summary.get("dataset_id") and dataset_matches:
            summary["dataset_id"] = view_dataset.paths[0].parent.name
        for row in scores:
            row["scoring_state"] = assessment_state(row)
            q = lookup.get(row["question_id"]) if dataset_matches else None
            row["question"] = q.row["input"] if q else None
            row["work_title"] = q.private["work"]["title"] if q else row["work"]
            row["images"] = [item.part()["image_url"]["url"] for item in q.images] if q else []
            if q and q.board == "story":
                row["reference"] = "总结参考：" + q.scoring["summary"]["reference_summary"]
                if not q.summary_only:
                    row["reference"] += ("\n作品：" + q.private["work"]["title"]
                        + "\n主要人物：" + "、".join(c["canonical"] for c in q.scoring["identification"]["character_entities"])
                        + "\n后续范围：" + q.scoring["prediction"]["horizon"])
            elif q:
                row["reference"] = q.scoring.get("reference_answer") or q.scoring.get("answer", {}).get("canonical", "")
            row["responses"] = {stage: {"text": prediction.get("response", {}).get("text", ""),
                "elapsed_seconds": prediction_seconds(prediction),
                "execution_state": prediction.get("state"), "executed": prediction.get("executed", True),
                "error_code": prediction.get("error_code"), "error_details": prediction.get("error_details"),
                "error": prediction.get("error"), "finish_reason": prediction.get("response", {}).get("finish_reason"),
                "warnings": prediction.get("response", {}).get("warnings", []),
                "usage": prediction.get("response", {}).get("usage", {})}
                for (qid, repeat, stage), prediction in latest.items()
                if qid == row["question_id"] and repeat == row["repeat"]}
            durations = [v["elapsed_seconds"] for v in row["responses"].values() if v.get("elapsed_seconds") is not None]
            row["answer_elapsed_seconds"] = sum(durations) if durations else row.get("answer_elapsed_seconds")
            if q: row["grading_route"] = grading_route(q)
            row["has_execution_issue"] = manual.has_execution_issue(row)
            row["has_manual_score"] = manual.has_manual_score(row)
            row["manual_contexts"] = {stage:manual.context(q,row["repeat"],stage,latest.get((q.id,row["repeat"],stage)),manifest["evaluation_hash"])
                for stage,value in row["stages"].items() if value.get("item_score") is None or value.get("method") == "manual_score"} if q else {}
        for board, group in summary["boards"].items():
            group.setdefault("answer_timing", timing_stats([r.get("answer_elapsed_seconds") for r in scores if r["board"] == board and r["status"] != "ERROR"]))
        summary["operations"].setdefault("answer_timing", timing_stats([prediction_seconds(p) for p in latest.values() if p["state"] == "ok"]))
        summary["operations"].setdefault("generation_wall_seconds", estimated_generation_wall(latest.values()))
        summary["operations"].setdefault("generation_wall_estimated", "generation_wall_seconds" not in manifest)
        summary["operations"]["manual_scored_samples"] = sum(manual.has_manual_score(r) for r in scores)
        summary["operations"]["manual_score_pending_samples"] = sum(r["item_score"] is None for r in scores)
        summary["operations"].setdefault("initial_target_concurrency", manifest.get("concurrency"))
        summary["operations"].setdefault("final_target_concurrency", manifest.get("target_final_concurrency", manifest.get("concurrency")))
        summary["concurrency_adjustments"] = manifest.get("concurrency_adjustments", [])
        return {"summary": summary, "scores": scores, "output": str(folder), "dataset_matches": dataset_matches,
                "judge": manifest.get("judge"),
                "files": sorted(name for name in FILES if (folder / name).is_file())}

    def manual_score(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("run_id"), str): raise ValueError("请指定存档")
        manual.validate_submission(payload)
        source = self.folder(payload["run_id"])
        with self.lock:
            if self.jobs.get(source.name, {}).get("state") in {"running","stopping"}: raise BusyError("请等待测试结束后再人工评分")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        dataset = next((d for d in self.datasets.values() if d.sha256 == manifest.get("grading_dataset_sha256",manifest["dataset_sha256"])),None)
        if dataset is None: raise ValueError("当前题库版本与存档不符，无法进行人工评分")
        repeat = integer(payload.get("repeat"), "轮次", 0, manifest["repeats"]-1)
        q = next((q for q in dataset.questions if q.id == payload.get("question_id")), None)
        stage = payload.get("stage")
        if not q or q.id not in manifest["question_ids"] or stage not in q.stages: raise ValueError("题目或阶段不属于此存档")
        row = next((r for r in read_jsonl(source/"scores.jsonl") if r["question_id"] == q.id and r["repeat"] == repeat), None)
        outcome = row.get("stages", {}).get(stage) if row else None
        if not outcome or (outcome.get("item_score") is not None and outcome.get("method") != "manual_score"):
            raise ValueError("仅允许为未完成评分的题目补分，或修订已有人工评分")
        predictions = {(r["question_id"],r["repeat"],r["stage"]):r for r in read_jsonl(source/"predictions.jsonl")}
        expected = manual.context(q,repeat,stage,predictions.get((q.id,repeat,stage)),manifest["evaluation_hash"])
        if payload.get("context") != expected: raise ValueError("题目、回答或评分标准已变化，请重新打开存档后再评分")
        record = {**expected,"score_percent":payload["score_percent"],"reason":payload["reason"].strip(),
                  "replacement_answer":payload.get("replacement_answer", ""),"reviewer":"local_user",
                  "recorded_at":timestamp(),"source_run":source.name}
        output = self.runs_root / ("manual-"+new_run_name())
        rescore(source,dataset,output,offline=True,manual_scores=[record],concurrency=4,progress=lambda _:None)
        return self.result(output.name)

    def questions_page(self, query):
        profile = query.get("profile", [self.default_dataset])[0]
        if profile not in self.datasets: raise ValueError("未知题库")
        board = query.get("board", ["all"])[0]
        if board not in {*BOARDS, "all"}: raise ValueError("未知榜单")
        page = integer(int(query.get("page", ["1"])[0]), "页码", 1, 10000)
        limit = integer(int(query.get("limit", ["20"])[0]), "每页题数", 1, 25)
        search = query.get("q", [""])[0].strip().casefold()[:100]
        questions = [q for q in self.datasets[profile].questions if q.row["split"] != "retired" and (board == "all" or q.board == board)]
        all_questions = [q for q in self.datasets[profile].questions if q.row["split"] != "retired"]
        work_titles = sorted({q.private["work"]["title"] for q in all_questions})
        works = [{"title":title,"count":sum(q.private["work"]["title"]==title for q in all_questions)} for title in work_titles]
        work = query.get("work",[""])[0][:200]
        if work: questions = [q for q in questions if q.private["work"]["title"]==work]
        developer = query.get("developer", ["all"])[0][:100]
        year = query.get("year", ["all"])[0]
        if year != "all" and (not year.isdigit() or not 1900 <= int(year) <= 2100):
            raise ValueError("无效作品年份")
        if developer != "all": questions = [q for q in questions if q.private["work"].get("developer") == developer]
        if year != "all": questions = [q for q in questions if q.private["work"].get("year") == int(year)]
        if search:
            questions = [q for q in questions if search in (q.id+" "+q.private["work"]["title"]+" "+json.dumps(q.row["input"], ensure_ascii=False)).casefold()]
        questions.sort(key=lambda q: (BOARDS.index(q.board), q.id))
        items = [{"id": q.id, "board": q.board, "category": q.category, "work_title": q.private["work"]["title"],
                  "input": q.row["input"], "images": [im.part()["image_url"]["url"] for im in q.images],
                  "grading_route": grading_route(q)} for q in questions[(page-1)*limit:page*limit]]
        return {"profile": profile, "total": len(questions), "page": page, "limit": limit,
                "pages": max(1, (len(questions)+limit-1)//limit), "items": items,"works":works,
                "collection_total":len(all_questions), "coverage":dataset_coverage(self.datasets[profile])}

    def preview_question(self, query):
        profile = query.get("profile",[self.default_dataset])[0]
        qid = query.get("id",[""])[0]
        if profile not in self.datasets: raise FileNotFoundError("未知题库")
        q = next((q for q in self.datasets[profile].questions if q.id==qid and q.row["split"]!="retired"),None)
        if q is None: raise FileNotFoundError("找不到题目")
        value = q.row["input"]
        if q.board=="story": text=" ".join(f"{t['speaker']}：{t['text']}" for t in value["excerpt"]["turns"][:2])
        else: text=value.get("prompt","")
        return {"id":q.id,"board":q.board,"work_title":q.private["work"]["title"],"excerpt":text[:160],
                "image_url":"/api/preview-image?"+urllib.parse.urlencode({"profile":profile,"id":q.id}) if q.images else None}

    def preview_image(self,query):
        preview=self.preview_question(query)
        dataset=self.datasets[query.get("profile",[self.default_dataset])[0]]
        q=next(q for q in dataset.questions if q.id==preview["id"])
        if not q.images: raise FileNotFoundError("本题没有图片")
        image=q.images[0];raw=image.path.read_bytes()
        if digest(raw)!=image.sha256: raise FileNotFoundError("题目图片已变化")
        return raw,image.mime

    def regrade(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("run_id"), str): raise ValueError("请指定存档")
        source = self.folder(payload["run_id"])
        with self.lock:
            if self.jobs.get(source.name, {}).get("state") in {"running","stopping"}: raise BusyError("请等待此测试结束后再评分")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        digest = manifest.get("grading_dataset_sha256", manifest["dataset_sha256"])
        dataset = next((d for d in self.datasets.values() if d.sha256 == digest), None)
        if dataset is None: raise ValueError("该存档题库版本当前不可用，请通过 CLI 指定原题库重新评分")
        use_judge = payload.get("use_judge", False)
        if type(use_judge) is not bool: raise ValueError("补判开关须为布尔值")
        judge, judge_key = None, None
        if use_judge:
            scores = read_jsonl(source / "scores.jsonl")
            if not any(assessment_state(row) == "judge_failed" for row in scores): raise ValueError("当前存档没有判分失败题目")
            settings = manifest.get("judge")
            if not isinstance(settings, dict): raise ValueError("此存档缺少原裁判配置，请通过 CLI 指定裁判重新评分")
            provided, judge_key = model_settings(payload.get("judge"), "裁判模型")
            if settings.get("provider") not in AGENTS:
                raise ValueError("此存档使用已移除的 API 裁判，请用 Agent 裁判重新评测")
            judge = AgentConfig(**{k:settings[k] for k in ("provider","executable","cli_model","supports_vision","timeout_seconds",
                "use_client_config","cli_profile","model_label","credential_env") if k in settings})
            if provided.public_dict() != judge.public_dict():
                raise ValueError("补判须沿用原裁判的 Harness、模型与运行配置")
            if manifest.get("judge_transport") != create_client(judge).info():
                raise ValueError("原裁判运行时已变化，请通过 CLI 完整重新评分")
            identity = judge.public_dict()
            if isinstance(judge, AgentConfig): identity = {**identity, "runtime": manifest["judge_transport"]}
            cache = {r.get("request_hash") for r in read_jsonl(source / "judgments.jsonl") if r.get("state") == "ok"}
            for row in scores:
                if assessment_state(row) != "scored": continue
                for stage in row["stages"].values():
                    if stage.get("method") in {"llm_judge", "judge_cache", "judge_recovered"} and fingerprint({
                        "request_hash": stage.get("review_hash"), "judge": identity}) not in cache:
                        raise ValueError("存档缺少已完成题目的裁判缓存；为避免额外调用，请通过 CLI 完整重新评分")
        output = self.runs_root / ("rescored-" + new_run_name())
        rescore(source, dataset, output, judge_config=judge, judge_api_key=judge_key, offline=not use_judge,
                concurrency=min(2 if use_judge else MAX_WEB_CONCURRENCY, manifest.get("concurrency", 4)), progress=lambda _: None)
        return self.result(output.name)


def make_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = "galbench-local"

        def log_message(self, *args):
            pass  # Access logs must not contain request bodies or credentials.

        def send_bytes(self, value, kind, status=200, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(value)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            try:
                self.end_headers()
                self.wfile.write(value)
            except ConnectionError:
                # A tab can leave while a cover or archive response is in flight.
                # This is a closed browser connection, not an evaluation failure.
                self.close_connection = True

        def json(self, value, status=200):
            self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                            "application/json; charset=utf-8", status)

        def valid_host(self):
            return self.headers.get("Host", "") in {
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.valid_host():
                return self.json({"error": "仅允许本机访问"}, 403)
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            try:
                if path == "/api/bootstrap":
                    return self.json(app.bootstrap())
                if path == "/api/agents":return self.json({"agents":discover_agents()})
                if path == "/api/history":
                    return self.json(app.history())
                if path == "/api/arena": return self.json(app.arena())
                if path == "/api/showcase": return self.json(app.showcase())
                if path == "/api/covers": return self.json(json.loads((STATIC/"covers/catalog.json").read_text(encoding="utf-8")))
                if path == "/api/preview": return self.json(app.preview_question(urllib.parse.parse_qs(parsed.query)))
                if path == "/api/preview-image":
                    raw,kind=app.preview_image(urllib.parse.parse_qs(parsed.query));return self.send_bytes(raw,kind)
                if path.startswith("/covers/"):
                    filename=path.removeprefix("/covers/")
                    if not re.fullmatch(r"[a-z0-9-]+\.jpg",filename): raise FileNotFoundError()
                    folder=(STATIC/"covers").resolve();file=(folder/filename).resolve()
                    if file.parent!=folder: raise FileNotFoundError()
                    return self.send_bytes(file.read_bytes(),"image/jpeg")
                if path.startswith("/ai-cast/"):
                    name=path.removeprefix("/ai-cast/")
                    if not re.fullmatch(r"(?:icons/)?[a-z0-9-]+\.(?:png|jpg|webp|svg)",name):raise FileNotFoundError()
                    folder=(STATIC/"ai-cast").resolve();file=(folder/name).resolve()
                    if not file.is_relative_to(folder):raise FileNotFoundError()
                    return self.send_bytes(file.read_bytes(),mimetypes.guess_type(name)[0] or "application/octet-stream")
                if path == "/api/questions":
                    try: return self.json(app.questions_page(urllib.parse.parse_qs(parsed.query)))
                    except (ValueError, TypeError) as exc: return self.json({"error": str(exc)}, 400)
                if path.startswith("/api/runs/"):
                    job_id = path.removeprefix("/api/runs/")
                    after = int(urllib.parse.parse_qs(parsed.query).get("after", ["0"])[0])
                    return self.json(app.snapshot(job_id, after))
                if path.startswith("/api/results/"):
                    return self.json(app.result(path.removeprefix("/api/results/")))
                if path.startswith("/api/files/"):
                    parts = path.removeprefix("/api/files/").split("/")
                    if len(parts) != 2 or parts[1] not in FILES:
                        raise FileNotFoundError()
                    folder = app.folder(parts[0])
                    file = (folder / parts[1]).resolve()
                    if file.parent != folder:
                        raise FileNotFoundError()
                    return self.send_bytes(file.read_bytes(), "application/octet-stream", filename=parts[1])
                asset = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/arena.js":"arena.js", "/arena.css":"arena.css", "/quest.js":"quest.js", "/quest.css":"quest.css", "/novel.js":"novel.js", "/novel.css":"novel.css", "/cast.js":"cast.js", "/cast.css":"cast.css", "/atelier.js":"atelier.js", "/atelier.css":"atelier.css", "/voxel.js":"voxel.js", "/voxel.css":"voxel.css", "/voxel-models.js":"voxel-models.js", "/voxel-cast.js":"voxel-cast.js", "/voxel-cast-data.js":"voxel-cast-data.js", "/voxel-renderer.js":"voxel-renderer.js", "/three.vendor.js":"three.vendor.js", "/favicon.svg": "favicon.svg"}.get(path)
                if not asset:
                    raise FileNotFoundError()
                kind = mimetypes.guess_type(asset)[0] or "application/octet-stream"
                return self.send_bytes((STATIC / asset).read_bytes(), kind + "; charset=utf-8")
            except (OSError, ValueError, KeyError):
                return self.json({"error": "找不到结果或文件，运行可能尚未完成"}, 404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if (not self.valid_host() or (origin is not None and origin not in expected)
                    or not secrets.compare_digest(self.headers.get("X-Galbench-Token", ""), app.token)):
                return self.json({"error": "本地会话已失效，请刷新页面"}, 403)
            if self.path not in {"/api/runs", "/api/runs/stop", "/api/clients/check", "/api/rescore", "/api/manual-score", "/api/arena/reset"}:
                return self.json({"error": "找不到此接口"}, 404)
            if self.headers.get_content_type() != "application/json":
                return self.json({"error": "需要 JSON 请求"}, 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= (131072 if self.path == "/api/manual-score" else 65536):
                    return self.json({"error": "请求体为空或过大"}, 413)
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/runs/stop": return self.json(app.stop(payload),202)
                if self.path == "/api/rescore": return self.json(app.regrade(payload))
                if self.path == "/api/manual-score": return self.json(app.manual_score(payload))
                if self.path == "/api/arena/reset": return self.json(app.reset_arena(payload))
                if self.path == "/api/clients/check":
                    if not isinstance(payload, dict): raise ValueError("请求必须是 JSON 对象")
                    if payload.get("provider") not in AGENTS:raise ValueError("请选择受支持的 Agent Harness")
                    if set(payload)-{"provider","executable","cli_model","supports_vision","timeout_seconds","use_client_config","cli_profile","model_label","credential_env"}:
                        raise ValueError("此入口只接收 Agent 配置，不接收直接 API 配置或密钥")
                    config = AgentConfig(**{k:payload[k] for k in ("provider","executable","cli_model","supports_vision","timeout_seconds",
                        "use_client_config","cli_profile","model_label","credential_env") if k in payload})
                    return self.json(AgentClient(config).check_runtime(require_images=config.supports_vision))
                return self.json(app.start(payload), 202)
            except BusyError as exc:
                return self.json({"error": str(exc)}, 409)
            except (OSError, KeyError):
                return self.json({"error": "找不到完整存档，请刷新历史记录后重试"}, 404)
            except (APIError, ValueError, TypeError, UnicodeError) as exc:
                result={"error":str(exc)}
                if isinstance(exc,APIError):result.update(error_code=exc.code,error_details=exc.details)
                return self.json(result, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(*, port=8765, open_browser=False):
    if not 1 <= port <= 65535:
        raise ValueError("port must be in [1,65535]")
    app = WebApp()
    try:
        server = make_server(app, port)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) == 98:
            raise ValueError(f"Port {port} is busy. Close the previous galbench window or use --port 8766.") from None
        raise
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"galbench local UI: {url}", flush=True)
    print("Keep this window open while testing. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
