"""Cooperative run cancellation and public, credential-free runtime diagnostics."""
from __future__ import annotations
import contextlib
import contextvars
import datetime as dt
import json
import re
import threading

CURRENT_CANCEL = contextvars.ContextVar("galbench_cancel", default=None)
CURRENT_RETRY = contextvars.ContextVar("galbench_retry", default=None)


class RunCancelled(Exception):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.RLock()
        self.callbacks = set()
        self.requested_at = None

    def check(self):
        if self.event.is_set(): raise RunCancelled("run_stopped_by_user")

    def cancel(self):
        with self.lock:
            if self.requested_at is None: self.requested_at = dt.datetime.now(dt.timezone.utc).isoformat()
            self.event.set()
            callbacks = list(self.callbacks)
        for callback in callbacks:
            try: callback()
            except Exception: pass

    def register(self, callback):
        with self.lock:
            cancelled = self.event.is_set()
            if not cancelled: self.callbacks.add(callback)
        if cancelled:
            try: callback()
            finally: self.check()
        def remove():
            with self.lock: self.callbacks.discard(callback)
        return remove

    def sleep(self, seconds):
        if self.event.wait(seconds): self.check()

    def io(self, operation):
        """Abandon a blocked DNS/connect/header wait without waiting for its timeout.

        Established response sockets are closed by registered callbacks. A late
        connection checks cancellation before reading its body and closes itself.
        The daemon can outlive this wait, but cannot issue a retry or another call.
        """
        self.check()
        done, result = threading.Event(), {}
        def work():
            try: result["value"] = operation()
            except BaseException as exc: result["error"] = exc
            finally: done.set()
        threading.Thread(target=work, daemon=True, name="galbench-http-wait").start()
        while not done.wait(.05): self.check()
        if "error" in result:
            self.check()
            raise result["error"]
        return result["value"]


@contextlib.contextmanager
def call_scope(cancel=None, retry=None):
    a, b = CURRENT_CANCEL.set(cancel), CURRENT_RETRY.set(retry)
    try:
        if cancel: cancel.check()
        yield
    finally:
        CURRENT_CANCEL.reset(a); CURRENT_RETRY.reset(b)


def issue_label(code):
    if code.startswith("http_"): return "模型接口返回 HTTP " + code.removeprefix("http_")
    if "timeout" in code: return "请求超时"
    if "quota" in code or "rate_limit" in code: return "服务商限流或额度不足"
    if "connection" in code: return "连接中断"
    if "tls" in code: return "TLS 连接异常"
    if "output_limit" in code: return "输出达到上限"
    if code == "judge_invalid_output": return "裁判评分格式异常"
    if code == "judge_refused": return "裁判拒绝评分"
    if code == "agent_reconnect_warning": return "CLI 重连告警"
    return "模型调用异常"


class RunMonitor:
    def __init__(self, output, on_event=None):
        self.output, self.on_event = output, on_event
        self.lock = threading.RLock()
        self.counts = {"total":0,"target":0,"judge":0,"warnings":0,"system":0}
        self.recent = []
        path = output / "runtime_events.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.counts["total"] += 1
                self.counts["warnings" if row["kind"]=="warning" else row["source"]] += 1
                self.recent = [row,*self.recent][:8]

    def snapshot(self):
        with self.lock: return {**self.counts,"recent":[dict(r) for r in self.recent]}

    def emit(self, value):
        if self.on_event: self.on_event(value)

    def issue(self, source, code, *, kind="error", question_id=None, board=None, repeat=0, stage=None):
        # Provider text, prompts and credential-bearing diagnostics never enter this stream.
        code = code if isinstance(code,str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}",code) else "call_failed"
        with self.lock:
            self.counts["total"] += 1
            self.counts["warnings" if kind=="warning" else source] += 1
            message = issue_label(code)
            if kind=="warning" and message=="模型调用异常": message="调用重试或重连告警"
            row = {"sequence":self.counts["total"],"at":dt.datetime.now(dt.timezone.utc).isoformat(),
                   "source":source,"kind":kind,"code":code,"message":message,
                   "question_id":question_id,"board":board,"repeat":repeat,"stage":stage}
            self.recent = [row,*self.recent][:8]
            with (self.output / "runtime_events.jsonl").open("a",encoding="utf-8") as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+"\n")
            # Serialize observer updates so a slow prior event cannot overwrite a newer count.
            self.emit({"type":"runtime_issue","issue":row,"issues":self.snapshot()})

    def retry(self, source, **context):
        return lambda code:self.issue(source,code,kind="warning",**context)
