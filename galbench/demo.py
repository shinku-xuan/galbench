"""Scripted local HTTP fixtures for protocol testing, never model capability claims."""
from __future__ import annotations
import base64
import contextlib
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .responses import normalize
from .data import fingerprint

def target_fixture(body):
    content = body["messages"][-1]["content"]
    parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
    text = next(p["text"] for p in parts if p["type"] == "text")
    task = json.loads(text)
    images = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
    if images:
        raw = base64.b64decode(images[0].split(",", 1)[1], validate=True)
        if not raw.startswith((b"\xff\xd8\xff", b"\x89PNG")): raise ValueError("No actual image bytes")
        portraits = {
            "20ddedd3cf79c34b16653d1a7fb910bfe098a9ef1cfc1cff4ce1665f04daf785": "古河渚",
            "fdb154b09d411007e32e0e35a420e1f85424a3780510838a8aa73316c89ca588": "藤林椋",
            "6d6eb7a514820261134fdd631a42b220055478055f7b25a74e9373b801366e64": "Tomoyo Sakagami",
        }
        return {"answer": portraits.get(hashlib.sha256(raw).hexdigest(), ""), "abstain": False}
    if "excerpt" in task:
        proposal = any("娶我" in t["text"] for t in task["excerpt"])
        if "summary_length" in task:
            if proposal:
                summary = "希尔薇主动求婚，叙述者答应，并说早就想求婚，只是担心时机；两人已经结婚。"
            else:
                summary = "叙述者犹豫着邀请希尔薇担任视觉小说画师，见她惊讶而担心。希尔薇愿意，但先询问视觉小说是什么。"
            return {"summary": summary, "abstain": False}
        if proposal:
            return {"work_guess": "The Question", "characters": ["希尔薇", "我", "艾琳"],
                    "continuation": [{"event": "两人不久后结婚", "basis": "remembered"},
                                     {"event": "两人结束合作，分道扬镳", "basis": "inference"}], "abstain": False}
        return {"work_guess": "", "characters": ["我", "希尔薇"],
                "continuation": [{"event": "叙述者解释视觉小说的图像、音乐和选择机制", "basis": "inference"},
                                 {"event": "希尔薇答应尝试做画师", "basis": "inference"}], "abstain": False}
    prompt = task["question"]
    if "双胞胎姐姐" in prompt: return "藤林杏"  # Correct plain text must retain knowledge credit.
    if "开头选择" in prompt: return {"answer": "他后来始终没能开口提出那个问题。", "abstain": False}
    if "配乐" in prompt: return {"answer": "音乐从大学讲堂的开场播放；叙述者惦记着向希尔薇提出一个问题。", "abstain": False}
    if "封印都市" in prompt: return {"answer": "城市因细菌攻击被放弃，但人类撤离后危险已经完全消失。", "abstain": False}
    return {"answer": "", "abstain": True}

def judge_fixture(request):
    kind, data = request["kind"], request["data"]
    candidate = data["candidate"]
    if kind == "entity":
        entity = data["accepted_entity"]
        return {"needs_review": False, "correct": normalize(candidate) in {normalize(a) for a in [entity["canonical"], *entity["aliases"]]},
                "support": candidate, "reason": "Scripted entity observation"}
    if kind == "atomic":
        prompt = data["question"]["prompt"]
        hits = {f["id"]: True for f in data["facts"]}
        errors = {c["id"]: False for c in data["contradictions"]}
        if "开头选择" in prompt: hits["f2"] = False
        if "封印都市" in prompt: hits["f2"] = False; errors["c1"] = True
        return {"needs_review": False, "fact_hits": hits, "contradictions": errors,
                "support": {key: candidate for mapping in (hits, errors) for key, active in mapping.items() if active},
                "reason": "Scripted partial-credit / contradiction fixture"}
    if kind == "summary":
        rubric = data["rubric"]
        proposal = "担心时机" in candidate
        hits = {f["id"]: not (proposal and f["id"] == "f1") for f in rubric["coverage_facts"]}
        relations = {r["id"]: not (proposal and r["id"] == "r1") for r in rubric["causal_relations"]}
        errors = {c["id"]: False for c in rubric["critical_errors"]}
        return {"needs_review": False, "coverage_hits": hits, "relation_hits": relations, "critical_errors": errors,
                "unsupported_facts": [{"quote": "两人已经结婚", "claim": "Future wedding stated as excerpt fact"}] if proposal else [],
                "expression": {"clarity": 5, "organization": 4, "compression": 4},
                "support": {key: candidate for mapping in (hits, relations) for key, active in mapping.items() if active},
                "reason": "Scripted summary dimensions; ratings are not an actual LLM judgment"}
    if kind == "inference":
        identity, prediction = data["identification"], data["prediction"]
        proposal = "marry-ending" in {a["id"] for a in prediction["alternatives"]}
        mapping = {}
        for i, name in enumerate(candidate["characters"]):
            mapping[str(i)] = next((e["entity_id"] for e in identity["character_entities"]
                if normalize(name) in {normalize(a) for a in [e["canonical"], *e["aliases"]]}), None)
        claims = []
        for i, event in enumerate(candidate["continuation"]):
            matches = {}
            for alternative in prediction["alternatives"]:
                aid, match = alternative["id"], None
                if "解释" in event["event"]: match = "p1"
                elif "画师" in event["event"]: match = "p2" if aid == "game" else "p3"
                elif "结婚" in event["event"]: match = "p2"
                matches[aid] = match
            claims.append({"prediction_index": i, "text": event["event"], "quote": event["event"], "matches": matches,
                           "extras": {a["id"]: None for a in prediction["alternatives"]}})
        return {"needs_review": False,
                "work_match": normalize(candidate["work_guess"]) in {normalize(a) for a in identity["work_answers"]},
                "character_matches": mapping,
                "grounding": {"evidence": 1 if proposal else 2, "causal": 1 if proposal else 2, "boundary": 1},
                "cue_hits": ["cue1"] if proposal else ["cue1", "cue2"], "predicted_claims": claims,
                "reason": "Scripted future-event matches"}
    raise ValueError("Unknown fixture kind")

@contextlib.contextmanager
def mock_server(dataset=None):
    calls = []
    fixtures = {}
    if dataset is not None:
        # Demo-only reference fixtures live in this mock endpoint, never in real model requests.
        for q in dataset.questions:
            for stage in q.stages:
                if q.board == "story": answer = {"summary":q.scoring["summary"]["reference_summary"],"abstain":False}
                else: answer = {"answer":q.scoring.get("reference_answer") or q.scoring["answer"]["canonical"],"abstain":False}
                fixtures[fingerprint(q.messages(stage))] = answer
    def observation(request):
        if dataset is None: return judge_fixture(request)
        kind, data = request["kind"], request["data"]
        candidate = data["candidate"]
        if kind == "summary":
            rubric = data["rubric"]
            hits = {f["id"]:True for f in rubric["coverage_facts"]}
            relations = {f["id"]:True for f in rubric["causal_relations"]}
            return {"needs_review":False,"coverage_hits":hits,"relation_hits":relations,
                    "critical_errors":{c["id"]:False for c in rubric["critical_errors"]},"unsupported_facts":[],
                    "expression":{"clarity":5,"organization":5,"compression":5},
                    "support":{key:candidate for key in [*hits,*relations]},
                    "reason":"Scripted reference-answer fixture, not an actual model judgment"}
        if kind == "atomic":
            hits = {f["id"]:True for f in data["facts"]}
            return {"needs_review":False,"fact_hits":hits,"contradictions":{c["id"]:False for c in data["contradictions"]},
                    "support":{key:candidate for key in hits},"reason":"Scripted reference-answer fixture"}
        return judge_fixture(request)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 30 * 1024 * 1024: raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size)); calls.append(body)
                value = (observation(json.loads(body["messages"][-1]["content"]))
                         if "judge" in body["model"] else fixtures.get(fingerprint(body["messages"])))
                if value is None: value = target_fixture(body)
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                result = {"id": "mock-galbench", "object": "chat.completion", "created": int(time.time()),
                    "model": body["model"] + "-NOT-A-REAL-MODEL",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
                raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            except (ValueError, KeyError, TypeError, IndexError):
                self.send_error(400, "Invalid mock request")
    class FixtureServer(ThreadingHTTPServer):
        request_queue_size = 64
    server = FixtureServer(("127.0.0.1", 0), Handler); server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .03}, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
