"""Versioned galbench datasets and an explicit model-input whitelist."""
from __future__ import annotations
import base64
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "galbench-1.1"
LEGACY_SCHEMA_VERSION = "galbench-1.0"
PROMPT_VERSION = "three-boards-1.1-summary-only"
TEXT_WEIGHTS = {"characters": .25, "plot_routes": .35, "music_narrative": .15, "world_rules": .25}
SYSTEM_PROMPT = "你正在参加 galbench。按题面范围作答，不使用外部工具，只提交最终答案；不知道的部分可以放弃。"
SCOPE_KEYS = {"work_title", "canon", "edition", "route", "story_point"}

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"))

def string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()

def image_mime(raw):
    if raw.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if raw.startswith((b"GIF87a", b"GIF89a")): return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP": return "image/webp"
    raise ValueError("Images must be JPEG, PNG, GIF or WebP, checked by signature")

@dataclass(frozen=True)
class ImageInput:
    path: Path
    sha256: str
    mime: str
    detail: str

    def part(self, redact=False):
        if redact:
            return {"type": "image_url", "sha256": self.sha256, "mime_type": self.mime, "detail": self.detail}
        raw = self.path.read_bytes()
        if digest(raw) != self.sha256: raise ValueError(f"Image changed after validation: {self.path}")
        return {"type": "image_url", "image_url": {
            "url": f"data:{self.mime};base64,{base64.b64encode(raw).decode('ascii')}", "detail": self.detail}}

@dataclass(frozen=True)
class Question:
    row: dict
    source: Path
    images: tuple[ImageInput, ...] = ()

    @property
    def id(self): return self.row["id"]
    @property
    def board(self): return self.row["board"]
    @property
    def version(self): return self.row["item_version"]
    @property
    def private(self): return self.row["private"]
    @property
    def scoring(self): return self.private["scoring"]
    @property
    def category(self): return self.private["category"]
    @property
    def work(self): return self.private["work"]["id"]
    @property
    def summary_only(self): return self.board == "story" and self.scoring["type"] == "story_summary"
    @property
    def score_policy(self): return self.private.get("score_policy", "category_weighted" if self.board == "text" else "item_mean")
    @property
    def stages(self): return (("a",) if self.summary_only else ("a", "b")) if self.board == "story" else ("answer",)

    def excerpt(self):
        return [{"speaker": t["speaker"], "text": t["text"]} for t in self.row["input"]["excerpt"]["turns"]]

    def messages(self, stage="answer", *, redact=False):
        if stage not in self.stages: raise ValueError("Invalid stage")
        visible = self.row["input"]
        if self.board == "story":
            instruction = ('返回 {"summary":"剧情总结","abstain":false}；整题放弃时 summary 留空且 abstain=true。' if stage == "a" else
                '返回 {"work_guess":"作品或留空","characters":["主要人物"],"continuation":[{"event":"预测事件","basis":"inference"}],"abstain":false}。'
                'basis 为 inference、remembered 或 unknown；最多三条预测，不列互斥猜测。')
            spec = visible if self.summary_only else visible["stage_" + stage]
            body = {"task": spec["prompt"], "excerpt": self.excerpt()}
            if stage == "a": body["summary_length"] = spec["summary_length"]
        else:
            instruction = '返回 {"answer":"一个答案或简短解释","abstain":false}；整题不知道时 answer 留空且 abstain=true。'
            body = {"question": visible["prompt"]}
            if self.board == "text": body["scope"] = {k: v for k, v in visible["scope"].items() if k in SCOPE_KEYS}
            if self.board == "vision" and "work_title" in visible: body["work_title"] = visible["work_title"]
        content = json.dumps(body, ensure_ascii=False)
        if self.images: content = [{"type": "text", "text": content}, *[im.part(redact) for im in self.images]]
        system = SYSTEM_PROMPT + instruction
        if self.board != "story" and self.private.get("score_policy") == "item_mean":
            system += "answer 只填写一个明确名称或词语，不解释、不列多个候选；使用作品中的全名。有多个译名时可用日文原名或罗马字全名。"
        if self.row["schema_version"] == SCHEMA_VERSION: system += "仅输出一个 JSON 对象。"
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    @property
    def public_signature(self):
        return fingerprint({s: self.messages(s, redact=True) for s in self.stages})

    @property
    def rubric_signature(self): return fingerprint(self.private)

@dataclass(frozen=True)
class Dataset:
    paths: tuple[Path, ...]
    sha256: str
    questions: tuple[Question, ...]

    @property
    def input_hash(self): return fingerprint({q.id: q.public_signature for q in self.questions})


def dataset_label(profile, dataset):
    active = [q for q in dataset.questions if q.row["split"] != "retired"]
    prefix = {"expanded": "广域开发集 2.0", "smoke": "流程样题", "custom": "自定义题库"}.get(profile, "题库")
    counts = " / ".join(str(sum(q.board == board for q in active)) for board in ("text", "vision", "story"))
    return f"{prefix} · {counts}"


def dataset_coverage(dataset):
    active = [q for q in dataset.questions if q.row["split"] != "retired"]
    works = {q.work: q.private["work"] for q in active}
    years = sorted({w["year"] for w in works.values() if type(w.get("year")) is int})
    developers = sorted({w["developer"] for w in works.values() if isinstance(w.get("developer"), str) and w["developer"]})
    return {"works": len(works), "developers": developers, "years": years,
            "boards": {b: {"works": len({q.work for q in active if q.board == b}),
                             "rules_only": all(q.scoring["type"] in {"exact_entity", "portrait_identity"} for q in active if q.board == b),
                             "score_policy": next((q.score_policy for q in active if q.board == b), None)}
                       for b in ("text", "vision", "story")}}

def _units(value, name, weighted=False):
    if not isinstance(value, list) or not value: raise ValueError(f"{name} must be a non-empty array")
    ids = []
    for unit in value:
        ids.append(string(unit.get("id"), name + ".id"))
        string(unit.get("claim"), name + ".claim")
        if weighted and (type(unit.get("weight")) not in (int, float) or not math.isfinite(unit["weight"]) or not 0 < unit["weight"] <= 1):
            raise ValueError("Invalid fact weight")
    if len(set(ids)) != len(ids): raise ValueError(f"{name}: duplicate IDs")
    if weighted and not math.isclose(sum(u["weight"] for u in value), 1, abs_tol=1e-8):
        raise ValueError(f"{name}: weights must sum to 1")

def _entity(value):
    for key in ("entity_id", "canonical"): string(value.get(key), "answer." + key)
    if not isinstance(value.get("aliases"), list): raise ValueError("aliases must be an array")
    for alias in value["aliases"]: string(alias, "alias")

def _validate(row, path):
    if row.get("schema_version") not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
        raise ValueError("Expected galbench-1.1 or legacy galbench-1.0; use spec/v1.1")
    for key in ("id", "item_version", "language"): string(row.get(key), key)
    board = row.get("board")
    if board not in {"text", "vision", "story"}: raise ValueError("Invalid board")
    if row.get("split") not in {"dev", "test", "retired"}: raise ValueError("Invalid split")
    private, visible = row["private"], row["input"]
    if not isinstance(private, dict) or not isinstance(visible, dict): raise ValueError("input/private must be objects")
    policy = private.get("score_policy", "category_weighted" if board == "text" else "item_mean")
    if policy not in {"category_weighted", "item_mean"} or (board != "text" and policy != "item_mean"):
        raise ValueError("Invalid board score policy")
    for key in ("category", "group_id"): string(private.get(key), "private." + key)
    for key in ("id", "title"): string(private["work"].get(key), "work." + key)
    if not isinstance(private["work"].get("aliases"), list) or not all(isinstance(a, str) for a in private["work"]["aliases"]):
        raise ValueError("work.aliases must be an array of strings")
    if not isinstance(private.get("scope"), dict): raise ValueError("private.scope must be an object")
    if private.get("difficulty") not in {"easy", "medium", "hard"}: raise ValueError("Invalid difficulty")
    if type(private.get("spoiler_level")) is not int or private["spoiler_level"] not in (0, 1, 2): raise ValueError("Invalid spoiler level")
    if type(private.get("sample_only")) is not bool: raise ValueError("sample_only must be boolean")
    review = private["review"]
    if review.get("status") not in {"draft", "source_checked", "verified", "disputed", "retired"}: raise ValueError("Invalid review status")
    reviewers = review.get("independent_reviewers")
    if not isinstance(reviewers, list) or not all(isinstance(r, str) and r.strip() for r in reviewers):
        raise ValueError("Reviewers must be identifiers")
    if type(review.get("eligible_for_leaderboard")) is not bool: raise ValueError("Invalid eligibility")
    if review["eligible_for_leaderboard"] and (private["sample_only"] or review["status"] != "verified" or len(set(reviewers)) < 2):
        raise ValueError("Leaderboard requires verified, two independent reviewers, not sample_only")
    evidence = private["evidence"]
    if not isinstance(evidence, list) or not evidence: raise ValueError("Evidence is required")
    eids = {string(e.get("id"), "evidence.id") for e in evidence}
    if len(eids) != len(evidence): raise ValueError("Duplicate evidence ID")
    for e in evidence:
        string(e.get("url"), "evidence.url"); string(e.get("locator"), "evidence.locator")
    scoring = private["scoring"]
    if scoring.get("rubric_version") not in {"galbench-rubric-1.0", "galbench-rubric-1.1"}: raise ValueError("Invalid rubric version")
    kind = scoring.get("type")
    images = []
    if board != "story":
        string(visible.get("prompt"), "input.prompt")
        allowed = {"prompt", "scope", "response_hint"} if board == "text" else {"prompt", "images", "response_hint", "work_title"}
        if set(visible) - allowed: raise ValueError("Unknown input field")
    if board == "text":
        if private["category"] not in TEXT_WEIGHTS or kind not in {"exact_entity", "atomic_facts"}: raise ValueError("Invalid text category/type")
        if not isinstance(visible.get("scope"), dict) or set(visible["scope"]) - SCOPE_KEYS: raise ValueError("Invalid public scope")
        if any(v is not None and not isinstance(v, str) for v in visible["scope"].values()): raise ValueError("Public scope values must be text or null")
    if kind in {"exact_entity", "portrait_identity"}: _entity(scoring["answer"])
    if kind == "atomic_facts":
        _units(scoring["facts"], "facts", True)
        if len({c.get("id") for c in scoring.get("contradictions", [])}) != len(scoring.get("contradictions", [])):
            raise ValueError("Duplicate contradiction ID")
        for c in scoring.get("contradictions", []):
            string(c.get("id"), "contradiction.id"); string(c.get("claim"), "contradiction.claim")
            if type(c.get("deduction")) not in (int, float) or not math.isfinite(c["deduction"]) or not 0 < c["deduction"] <= 1:
                raise ValueError("Invalid contradiction deduction")
    if board == "vision":
        if kind != "portrait_identity" or not visible.get("images"): raise ValueError("Vision 1.0 requires portraits and images")
        if row["schema_version"] == SCHEMA_VERSION or "work_title" in visible:
            title = string(visible.get("work_title"), "input.work_title")
            if title not in {private["work"]["title"], *private["work"]["aliases"]}:
                raise ValueError("Vision work_title must match a verified work name")
        for image in visible["images"]:
            relative = Path(string(image.get("path"), "image.path"))
            target = (path.parent / relative).resolve()
            if relative.is_absolute() or not target.is_relative_to(path.parent): raise ValueError("Image must stay inside dataset directory")
            raw = target.read_bytes()
            if not 0 < len(raw) <= 20 * 1024 * 1024: raise ValueError("Invalid image size")
            if digest(raw) != image.get("sha256"): raise ValueError("Image SHA-256 mismatch")
            mime = image_mime(raw)
            if image.get("mime_type") != mime or image.get("detail") not in {"auto", "low", "high"}: raise ValueError("Invalid image metadata")
            images.append(ImageInput(target, digest(raw), mime, image["detail"]))
    if board == "story":
        only_summary = kind == "story_summary"
        if row["schema_version"] == SCHEMA_VERSION:
            if not only_summary or set(visible) - {"excerpt", "prompt", "summary_length", "response_hint"}:
                raise ValueError("galbench-1.1 story requires summary-only input")
            if set(scoring) - {"type", "rubric_version", "summary"} or scoring["rubric_version"] != "galbench-rubric-1.1":
                raise ValueError("Summary-only rubric must not contain identification or prediction tasks")
        elif kind != "story_v1" or set(visible) - {"excerpt", "stage_a", "stage_b"}:
            raise ValueError("Invalid legacy story input/type")
        turns = visible["excerpt"]["turns"]
        if not isinstance(turns, list) or not turns: raise ValueError("Story requires turns")
        tids = []
        for turn in turns:
            for key in ("turn_id", "speaker", "text"): string(turn.get(key), "turn." + key)
            tids.append(turn["turn_id"])
        if len(set(tids)) != len(tids): raise ValueError("Duplicate turn ID")
        if only_summary: string(visible.get("prompt"), "input.prompt")
        else:
            for s in ("a", "b"): string(visible["stage_" + s].get("prompt"), "stage prompt")
        summary = scoring["summary"]
        for key in ("coverage_facts", "causal_relations"): _units(summary[key], key, True)
        length = summary["length"]
        if (type(length.get("min")) is not int or type(length.get("max")) is not int or not 0 < length["min"] <= length["max"]
                or (visible if only_summary else visible["stage_a"]).get("summary_length") != length): raise ValueError("Mismatched summary length")
        for c in summary.get("critical_errors", []):
            if c.get("deduction") != (12 if only_summary else 10): raise ValueError("Invalid core-error deduction for this protocol")
        if len({c.get("id") for c in summary.get("critical_errors", [])}) != len(summary.get("critical_errors", [])):
            raise ValueError("Duplicate critical error ID")
        if not only_summary:
            identity = scoring["identification"]
            if not identity.get("work_answers") or not identity.get("character_entities"): raise ValueError("Missing story identity rubric")
            if not isinstance(identity["work_answers"], list) or not all(isinstance(a, str) and a.strip() for a in identity["work_answers"]):
                raise ValueError("work_answers must be an array of non-empty strings")
            for entity in identity["character_entities"]: _entity(entity)
            if len({e["entity_id"] for e in identity["character_entities"]}) != len(identity["character_entities"]): raise ValueError("Duplicate character entity")
            alts = scoring["prediction"]["alternatives"]
            if not alts or len({a["id"] for a in alts}) != len(alts): raise ValueError("Invalid prediction alternatives")
            for alt in alts: _units(alt["events"], "events")
    def refs(value):
        if isinstance(value, dict):
            if "evidence_ids" in value and not set(value["evidence_ids"]) <= eids: raise ValueError("Unknown evidence reference")
            if board == "story" and "turn_ids" in value and not set(value["turn_ids"]) <= set(tids): raise ValueError("Unknown turn reference")
            for v in value.values(): refs(v)
        elif isinstance(value, list):
            for v in value: refs(v)
    refs(scoring)
    return Question(row, path, tuple(images))

def load_dataset(paths):
    supplied = paths if isinstance(paths, (list, tuple)) else [paths]
    files = []
    for source in supplied:
        path = Path(source).resolve()
        files.extend(sorted(path.glob("*.jsonl")) if path.is_dir() else [path])
    if not files or len(set(files)) != len(files): raise ValueError("No datasets or duplicate input files")
    questions, hashes, seen, groups = [], [], set(), {}
    for path in files:
        raw = path.read_bytes(); hashes.append(digest(raw))
        for line_no, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
            if not line.strip(): continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict): raise ValueError("Each JSONL line must be an object")
                q = _validate(row, path)
                if q.id in seen: raise ValueError("Duplicate question ID: " + q.id)
                group = q.private["group_id"]
                if group in groups and groups[group] != row["split"]: raise ValueError("A group spans dataset splits")
                groups[group] = row["split"]; seen.add(q.id); questions.append(q)
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                raise ValueError(f"{path.name}:{line_no}: {exc}") from exc
    if not questions: raise ValueError("Dataset contains no questions")
    if len({q.summary_only for q in questions if q.board == "story"}) > 1:
        raise ValueError("Cannot mix summary-only and legacy story protocols in one dataset")
    if len({q.score_policy for q in questions if q.board == "text"}) > 1:
        raise ValueError("Cannot mix text aggregation policies in one dataset")
    return Dataset(tuple(files), fingerprint(hashes), tuple(questions))
