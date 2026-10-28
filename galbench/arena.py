"""Local season standings and a read-only catalog of evaluation archives."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

from .data import fingerprint, dataset_label
from .identity import model_identity
from .reporting import partial_score, percent_summary

SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
BOARD_NAMES = {"text":"游戏内知识", "vision":"角色识图", "story":"剧情总结"}


def total_score(boards):
    values = [boards.get(b) for b in BOARD_NAMES]
    complete = all(v and v["complete"] for v in values)
    references = [v["score"] if v["complete"] else v["reference_score"] for v in values if v]
    return {"score":round(sum(v["score"] for v in values), 6) if complete else None,
            "reference_score":round(sum(references), 6) if len(references)==3 and all(v is not None for v in references) else None,
            "reference_kind":"three_board_reference", "complete":complete,
            "manual":any(v and v["manual"] for v in values), "max_score":300,
            "completed_boards":sum(bool(v and v["complete"]) for v in values),
            "scored_samples":sum(v["scored_samples"] for v in values if v),
            "samples":sum(v["samples"] for v in values if v)}


def vendor_for(model, provider=""):
    name = model.casefold().split("/")[-1]
    for prefixes, label in [(("gpt","codex","o1","o3","o4"),"OpenAI"),(("deepseek",),"DeepSeek"),
        (("claude",),"Anthropic"),(("gemini",),"Google"),(("qwen",),"通义千问"),(("glm","zcode"),"智谱"),
        (("minimax","mcode"),"MiniMax"),(("kimi","moonshot"),"月之暗面"),(("grok",),"xAI"),
        (("llama",),"Meta"),(("mistral","magistral"),"Mistral"),(("doubao",),"豆包")]:
        if name.startswith(prefixes): return label
    return "其他 / 自定义"  # An Agent client is not evidence of the underlying model vendor.


def finite_score(value):
    return value if type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 100 else None


def catalog(root, datasets):
    root = Path(root).resolve()
    profiles = {d.sha256:{"id":key,"label":dataset_label(key,d)}
                for key,d in datasets.items()}
    rows, skipped = [], 0
    for folder in root.iterdir() if root.exists() else []:
        if not folder.is_dir() or not SAFE_ID.fullmatch(folder.name) or folder.resolve().parent != root: continue
        if not (folder/"summary.json").is_file() or not (folder/"manifest.json").is_file(): continue
        try:
            summary = percent_summary(json.loads((folder/"summary.json").read_text(encoding="utf-8")))
            manifest = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
            model = summary["model"]
            if not isinstance(model,str) or not isinstance(summary["boards"],dict) or not isinstance(summary["scored_at"],str): raise ValueError("Invalid archive")
            mode = summary.get("mode","api")
            if any("NOT-A-REAL-MODEL" in str(v) or "LOCAL-FIXTURE" in str(v) for v in summary.get("returned_models",[])): mode = "mock_demo"
            if mode == "mock_demo": continue  # Demo archives never enter public standings or history.
            provider = manifest.get("model",{}).get("provider","openai_compatible")
            identity = model_identity(summary, manifest.get("model",{}))
            model = identity["name"]
            sha = manifest.get("grading_dataset_sha256",manifest.get("dataset_sha256","unknown"))
            profile = profiles.get(sha,{"id":"archive-"+fingerprint(str(sha))[:10],"label":"归档题库 · "+str(summary.get("dataset_id") or manifest.get("dataset_id") or "旧版")})
            profiles.setdefault(sha,profile)
            boards = {}
            old_scores = None
            for board,group in summary["boards"].items():
                if board not in BOARD_NAMES: continue
                score = finite_score(group.get("gal_score"))
                if score is None and "display_score" not in group and (folder/"scores.jsonl").is_file():
                    if old_scores is None: old_scores = [json.loads(line) for line in (folder/"scores.jsonl").read_text(encoding="utf-8").splitlines() if line]
                    group = {**group,**partial_score(board,[r for r in old_scores if r.get("board")==board])}
                reference = group.get("provisional_score") if group.get("provisional_score") is not None else group.get("display_score")
                boards[board] = {"score":score,"reference_score":finite_score(reference),"reference_kind":group.get("display_score_kind"),
                    "complete":score is not None,"manual":bool(group.get("manual_scored_samples",0)),
                    "scored_samples":group.get("scored_samples",group.get("samples",0)-group.get("pending_samples",0)),
                    "samples":group.get("samples",0)}
            identity_key = {"model":model.casefold(), "mode":mode, "transport":provider}
            if mode == "agent_cli":
                settings = manifest.get("model",{})
                identity_key["route"] = {"selection":settings.get("cli_model", ""),
                    "client_config":settings.get("use_client_config",False), "profile":settings.get("cli_profile", ""),
                    "reported_providers":summary.get("returned_providers", [])}
            if identity["source"] in {"unknown", "mixed"}: identity_key["unresolved_run"] = folder.name
            rows.append({"run_id":folder.name,"model":model,"model_key":fingerprint(identity_key)[:20],
                "model_identity":identity,
                "vendor":vendor_for(model,provider),"vendor_inferred":True,"mode":mode,"transport":provider,
                "status":summary["status"],"stopped":bool(summary.get("stopped")),"result_state":summary.get("result_state"),"scored_at":summary["scored_at"],
                "source_run":summary.get("source_run") or manifest.get("source_run"),"dataset_key":profile["id"],"dataset_label":profile["label"],
                "boards":boards,"total":total_score(boards),"repeats":manifest.get("repeats",1),"generation_seconds":summary.get("operations",{}).get("generation_wall_seconds"),
                "manual":any(b["manual"] for b in boards.values())})
        except (OSError,ValueError,KeyError,TypeError,AttributeError): skipped += 1
    lookup = {r["run_id"]:r for r in rows}
    def origin(row):
        seen = set()
        while row["run_id"] not in seen:
            seen.add(row["run_id"])
            parent = str(row.get("source_run") or "").replace("\\","/").rstrip("/").rsplit("/",1)[-1]
            if not parent or parent not in lookup: return parent if parent and SAFE_ID.fullmatch(parent) else row["run_id"]
            row = lookup[parent]
        return min(seen)
    for row in rows: row["root_run_id"] = origin(row)
    return sorted(rows,key=lambda r:r["scored_at"],reverse=True),list(profiles.values()),skipped


def standings(records, hidden_roots):
    # A regrade updates one match; it does not create another independent trial.
    latest = {}
    for row in sorted(records,key=lambda r:r["scored_at"],reverse=True):
        latest.setdefault(row["root_run_id"],row)
    candidates = defaultdict(list)
    for row in latest.values():
        if row["root_run_id"] in hidden_roots or row.get("mode") == "mock_demo": continue
        for board,value in {**row["boards"], "total":row["total"]}.items():
            candidates[row["model_key"],row["dataset_key"],board].append({**row,**value,"board":board,
                "max_score":300 if board=="total" else 100,
                "eligible":value["complete"] and not value["manual"] and not row.get("stopped")
                    and row.get("model_identity",{}).get("source") not in {"unknown","mixed"}})
    entries = []
    for group in candidates.values():
        def value(row):
            return (2 if row["eligible"] else 1 if row["complete"] else 0,
                row["score"] if row["score"] is not None else row["reference_score"] if row["reference_score"] is not None else -1,
                row["scored_at"])
        chosen = max(group,key=value)
        entries.append({**chosen,"matches":len(group),"rank":None})
    divisions = defaultdict(list)
    for entry in entries:
        if entry["eligible"]: divisions[entry["dataset_key"],entry["board"],entry["mode"]].append(entry)
    for group in divisions.values():
        group.sort(key=lambda r:(-r["score"],r["model"].casefold()))
        previous,rank = None,0
        for position,row in enumerate(group,1):
            if row["score"] != previous: rank = position
            row["rank"] = rank; previous = row["score"]
    return entries
