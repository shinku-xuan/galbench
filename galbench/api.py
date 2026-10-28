"""Error types and loopback-only HTTP fixtures; real models use Agent Harnesses."""

from __future__ import annotations

import json
import http.client
import math
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from .control import CURRENT_CANCEL, CURRENT_RETRY

DEFAULT_MAX_TOKENS = 32768
DEFAULT_TIMEOUT_SECONDS = 300
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


class APIError(RuntimeError):
    def __init__(self, message, *, code=None, fatal=False, details=None):
        super().__init__(message)
        self.code = code
        self.fatal = fatal
        self.details = details


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward an Authorization header to an unexpected destination.
        return None


def request_opener(cancel=None):
    if cancel is None: return urllib.request.build_opener(NoRedirect())
    def controlled(base):
        class Connection(base):
            def connect(self):
                cancel.check()
                super().connect()
                def abort():
                    if self.sock:
                        try: self.sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        self.sock.close()
                self._cancel_remove = cancel.register(abort)
                cancel.check()
            def close(self):
                try: super().close()
                finally:
                    if remove := getattr(self,"_cancel_remove",None):
                        self._cancel_remove = None; remove()
        return Connection
    http_connection,https_connection = controlled(http.client.HTTPConnection),controlled(http.client.HTTPSConnection)
    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self,req): return self.do_open(http_connection,req)
    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self,req): return self.do_open(https_connection,req,context=self._context)
    return urllib.request.build_opener(NoRedirect(),HTTPHandler(),HTTPSHandler())


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    name: str
    api_key_env: str | None = "GALBENCH_API_KEY"
    supports_vision: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = 2
    parameters: dict = field(default_factory=lambda: {"max_tokens": DEFAULT_MAX_TOKENS})

    def __post_init__(self):
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("直接模型 API 接口已移除，请通过 Agent Harness 接入；HTTP 仅供本地模拟测试")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model.base_url must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, a query or a fragment")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("model.name must be non-empty")
        if not isinstance(self.supports_vision, bool):
            raise ValueError("supports_vision must be a boolean")
        if (isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
                or type(self.max_retries) is not int or not 0 <= self.max_retries <= 5):
            raise ValueError("timeout must be positive; max_retries must be in [0, 5]")
        if self.api_key_env is not None and not isinstance(self.api_key_env, str):
            raise ValueError("api_key_env must be an environment variable name or empty")
        if not isinstance(self.parameters, dict):
            raise ValueError("parameters must be a configuration table")
        forbidden = {"model", "messages", "stream", "n", "tools", "tool_choice",
                     "functions", "function_call", "web_search_options", "store",
                     "api_key", "authorization", "headers"}
        if forbidden.intersection(self.parameters):
            raise ValueError("parameters cannot override model/messages or enable tools/streaming")
        if "max_tokens" in self.parameters and "max_completion_tokens" in self.parameters:
            raise ValueError("Use only one of max_tokens and max_completion_tokens")
        for name in ("max_tokens", "max_completion_tokens"):
            if name in self.parameters and (
                type(self.parameters[name]) is not int or self.parameters[name] < 1
            ):
                raise ValueError(f"{name} must be a positive integer")

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def public_dict(self) -> dict:
        return {"base_url": self.base_url, "name": self.name,
                "api_key_env": self.api_key_env, "supports_vision": self.supports_vision,
                "timeout_seconds": self.timeout_seconds, "max_retries": self.max_retries,
                "parameters": self.parameters}


class ChatClient:
    def __init__(self, config: ModelConfig, *, api_key: str | None = None):
        self.config = config
        self._key = api_key if api_key is not None else (os.environ.get(config.api_key_env, "") if config.api_key_env else "")
        if not isinstance(self._key, str):
            raise ValueError("API key must be a string")
        if api_key is None and config.api_key_env and not self._key:
            raise ValueError(f"Missing environment variable: {config.api_key_env}")
        if any(ord(char) < 32 or ord(char) == 127 for char in self._key):
            raise ValueError("Invalid API key characters")

    def complete(self, messages: list[dict]) -> dict:
        payload = {"model": self.config.name, "messages": messages,
                   **self.config.parameters}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        started = time.perf_counter()
        retries = []
        cancel = CURRENT_CANCEL.get()
        def failure(message, code, attempt, *, retryable=False, fatal=False, **details):
            return APIError(message, code=code, fatal=fatal, details={"retryable": retryable,
                "http_attempts": attempt+1, "elapsed_seconds": time.perf_counter()-started,
                "retries": list(retries), **details})
        for attempt in range(self.config.max_retries + 1):
            if cancel: cancel.check()
            delay = min(.25 * 2 ** attempt, 4)
            request = urllib.request.Request(self.config.endpoint, body, headers, method="POST")
            try:
                def receive():
                    if cancel: cancel.check()
                    with request_opener(cancel).open(request, timeout=self.config.timeout_seconds) as response:
                        remove = None
                        def abort():
                            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                            if sock:
                                try: sock.shutdown(socket.SHUT_RDWR)
                                except OSError: pass
                            # HTTPResponse.close() takes the buffered reader lock;
                            # the reader owns cleanup after shutdown wakes its I/O.
                        try:
                            if cancel:
                                remove = cancel.register(abort)
                                cancel.check()
                            return response.read(16 * 1024 * 1024 + 1)
                        finally:
                            if remove: remove()
                raw = cancel.io(receive) if cancel else receive()
                if len(raw) > 16 * 1024 * 1024:
                    raise APIError("API response exceeded 16 MiB")
                try:
                    def reject_nonfinite(value):
                        raise ValueError("Non-finite JSON number")
                    result = json.loads(raw, parse_constant=reject_nonfinite)
                    choice = result["choices"][0]
                    message = choice["message"]
                    if not isinstance(message, dict) or not isinstance(result.get("usage", {}), (dict, type(None))):
                        raise ValueError("Invalid message or usage object")
                    text = message.get("content")
                    if text is not None and not isinstance(text, str):
                        raise ValueError("Expected message.content to be text")
                    if message.get("tool_calls") or message.get("function_call"):
                        raise ValueError("Tool calls are not allowed in the closed-book track")
                    if text is None and not message.get("refusal") and choice.get("finish_reason") not in {"length", "content_filter"}:
                        raise ValueError("No final answer or refusal in API response")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                    raise APIError("Invalid Chat Completions response shape") from exc
                return {"text": text or "", "refusal": message.get("refusal"),
                        "finish_reason": choice.get("finish_reason"),
                        "returned_model": result.get("model"),
                        "usage": result.get("usage") or {}, "raw_response": result,
                        "elapsed_seconds": time.perf_counter() - started,
                        "http_attempts": attempt + 1, "http_retries": retries}
            except urllib.error.HTTPError as exc:
                status = exc.code
                try: delay = max(delay, min(float(exc.headers.get("Retry-After", "0")), 10))
                except (TypeError, ValueError): pass
                exc.close()
                retryable = status in RETRYABLE_HTTP_STATUSES
                code = f"http_{status}"
                if not retryable or attempt == self.config.max_retries:
                    # A provider may echo secrets in its error body; never log that body.
                    raise failure(f"HTTP {status} from model endpoint", code, attempt, retryable=retryable,
                        fatal=status in {400, 401, 403, 404, 405, 410, 422}, http_status=status) from None
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, ssl.SSLError,
                    http.client.IncompleteRead, http.client.BadStatusLine) as exc:
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise failure("Model endpoint TLS certificate verification failed", "tls_certificate_error", attempt, fatal=True) from None
                code = "request_timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "connection_error"
                if attempt == self.config.max_retries:
                    raise failure("Model endpoint connection failed or timed out", code, attempt, retryable=True) from None
            retries.append({"attempt": attempt+1, "error_code": code, "delay_seconds": delay})
            if cancel: cancel.check()
            if retry := CURRENT_RETRY.get(): retry(code)
            if cancel: cancel.sleep(delay)
            else: time.sleep(delay)
        raise APIError("Request did not complete")
