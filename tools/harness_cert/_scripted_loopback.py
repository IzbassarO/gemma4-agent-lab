"""Bounded loopback transport for synthetic, nonstreaming harness probes.

Importing this module uses only the standard library. The URL guard constrains
this SDK client's requests; it does not restrict networking by tool subprocesses.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any
from urllib.parse import urlsplit

from ._probe_safety import IPV4_SERVER, ip_literal


def _origin(url: str) -> str:
    if not isinstance(url, str) or any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValueError("URL must be an explicit HTTP loopback URL")
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid loopback port") from exc
    if (
        parsed.scheme != "http"
        or ip_literal(parsed.hostname) != IPV4_SERVER
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.netloc != f"127.0.0.1:{port}"
        or parsed.query
        or parsed.fragment
        or "\\" in url
    ):
        raise ValueError("URL must use http://127.0.0.1:<port> without credentials")
    return f"http://127.0.0.1:{port}"


def validate_loopback_url(url: str, expected_origin: str | None = None) -> str:
    """Validate a literal loopback /v1 URL and return it unchanged.

    Hostnames, credentials, queries, fragments, escapes, traversal and any other
    origin are rejected before a client can issue a request.
    """
    origin = _origin(url)
    path = urlsplit(url).path
    if (
        not (path == "/v1" or path.startswith("/v1/"))
        or "%" in path
        or "//" in path
        or any(component in {".", ".."} for component in path.split("/"))
    ):
        raise ValueError("Only explicit /v1 paths are allowed")
    if expected_origin is not None:
        if urlsplit(expected_origin).path not in {"", "/"}:
            raise ValueError("Expected origin must not contain a path")
        if origin != _origin(expected_origin):
            raise ValueError("Request attempted to leave the scripted loopback origin")
    return url


async def create_client(base_url: str) -> tuple[Any, Any]:
    """Create an AsyncOpenAI client and its owned, guarded HTTP client.

    Callers must close both clients. Dependencies are imported only here; no
    package import or socket connection occurs when this module is imported.
    """
    validate_loopback_url(base_url)
    if urlsplit(base_url).path.rstrip("/") != "/v1":
        raise ValueError("Client base_url must end in /v1")
    origin = _origin(base_url)

    import httpx
    from openai import AsyncOpenAI

    class SharedTransportClient(AsyncOpenAI):
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            # The exact compiler deep-copies LiteLlm configuration. HTTP clients
            # contain locks and are owned by this probe, not by compiled agents.
            memo[id(self)] = self
            return self

    guard_violations: list[str] = []

    async def guard_request(request: Any) -> None:
        try:
            validate_loopback_url(str(request.url), expected_origin=origin)
        except ValueError as exc:
            # Preserve transport refusals even if ADK converts the exception to
            # an agent error. Validation messages contain no URL credentials.
            guard_violations.append(str(exc))
            raise

    http_client = httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(10.0),
        event_hooks={"request": [guard_request]},
    )
    http_client.probe_guard_violations = guard_violations
    try:
        client = SharedTransportClient(
            base_url=base_url.rstrip("/"),
            api_key="H23_SCRIPTED_DUMMY",
            max_retries=0,
            timeout=10.0,
            http_client=http_client,
        )
        # OpenAI's optional get_platform() calls platform.platform(), which on
        # CPython 3.12 Darwin runs `uname -p` with DEVNULL stderr. Preserve the
        # recorded fallback header without attempting this unapproved process.
        # This instance is shared across compiler copies; no global SDK patch.
        client._platform = "Unknown"
    except BaseException:
        await http_client.aclose()
        raise
    return client, http_client


class _NumericLoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind calls getfqdn, which can trigger reverse DNS.
        # A numeric loopback listener needs only the socket's bound address.
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]


class ScriptedServer:
    """Context-managed 127.0.0.1 server returning supplied assistant messages.

    ``set_script`` accepts assistant message dictionaries in OpenAI format.
    Requests consume one message each. Invalid requests and exhausted scripts
    receive HTTP errors and never fall back to another endpoint.
    """

    def __init__(self, *, max_request_bytes: int = 64 * 1024, request_timeout: float = 3.0):
        if (
            not isinstance(max_request_bytes, int)
            or isinstance(max_request_bytes, bool)
            or max_request_bytes <= 0
            or not math.isfinite(request_timeout)
            or request_timeout <= 0
        ):
            raise ValueError("Request bounds must be positive")
        self.max_request_bytes = max_request_bytes
        self.request_timeout = request_timeout
        self.calls: list[dict[str, Any]] = []
        self._script: list[dict[str, Any]] = []
        self._position = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._held_number: int | None = None
        self._held_wait = 8.0
        self._held_admitted = threading.Event()
        self._held_release = threading.Event()
        self._held_done = threading.Event()

    @property
    def origin(self) -> str:
        if self._server is None:
            raise RuntimeError("Scripted server is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def base_url(self) -> str:
        return self.origin + "/v1"

    def set_script(self, messages: list[dict[str, Any]]) -> None:
        if self._held_admitted.is_set() and not self._held_done.is_set():
            raise RuntimeError("Previous withheld response has not finished")
        if not isinstance(messages, list):
            raise ValueError("Script must be a list of assistant messages")
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Each scripted message must have role=assistant")
            if not ("content" in message or "tool_calls" in message):
                raise ValueError("Assistant message must contain content or tool_calls")
        # Also reject values that cannot be represented in the recorded wire JSON.
        json.dumps(messages, allow_nan=False)
        with self._lock:
            self._script = copy.deepcopy(messages)
            self._position = 0
            self._held_number = None
            self._held_admitted.clear()
            self._held_release.clear()
            self._held_done.clear()

    def withhold_response(self, number: int, *, max_wait: float = 8.0) -> None:
        """Record one request, withholding its response until operator release.

        This finite synthetic delay tests the runner's actual async deadline.
        The designated response is never sent, including after cancellation.
        Other requests retain the existing immediate-response semantics.
        """
        if (type(number) is not int or not 1 <= number <= len(self._script)
                or self._position != 0 or self._held_number is not None
                or not math.isfinite(max_wait) or not 0 < max_wait <= 8.0):
            raise ValueError("Withholding requires one unconsumed script response and a bounded wait")
        self._held_number, self._held_wait = number, max_wait

    def release_withheld_response(self) -> bool:
        self._held_release.set()
        return not self._held_admitted.is_set() or self._held_done.wait(timeout=1.0)

    def __enter__(self) -> ScriptedServer:
        if self._server is not None:
            raise RuntimeError("Scripted server is already running")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(owner.request_timeout)

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _respond(self, status: int, payload: dict[str, Any], body: bytes = b"", *, script_number=None) -> None:
                response_body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
                response_headers = [
                    ["Content-Type", "application/json"],
                    ["Content-Length", str(len(response_body))],
                    ["Connection", "close"],
                ]
                call = {
                    "request": {
                        "method": self.command,
                        "path": self.path,
                        "http_version": self.request_version,
                        "peer": self.client_address[0],
                        "headers": [[name, value] for name, value in self.headers.items()],
                        "body_text": body.decode("utf-8", errors="replace"),
                        "body_base64": base64.b64encode(body).decode("ascii"),
                        "body_complete": self.headers.get("Content-Length") == str(len(body)),
                    },
                    "response": {
                        "status": status,
                        "headers": response_headers,
                        "body_text": response_body.decode("utf-8"),
                        "body_base64": base64.b64encode(response_body).decode("ascii"),
                        "json": payload,
                        "delivery": "pending",
                    },
                    "script_number": script_number,
                }
                try:
                    call["request"]["json"] = json.loads(body)
                except (ValueError, UnicodeError):
                    pass
                with owner._lock:
                    owner.calls.append(call)
                if script_number is not None and script_number == owner._held_number:
                    call["response"]["delivery"] = "withheld"
                    owner._held_admitted.set()
                    released = owner._held_release.wait(timeout=owner._held_wait)
                    call["response"]["delivery"] = "discarded_after_release" if released else "hold_bound_expired"
                    self.close_connection = True
                    owner._held_done.set()
                    return
                self.send_response_only(status)
                for name, value in response_headers:
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(response_body)
                    call["response"]["delivery"] = "sent"
                except (BrokenPipeError, ConnectionResetError) as exc:
                    call["response"]["delivery"] = type(exc).__name__
                self.close_connection = True

            def _error(self, status: int, message: str, body: bytes = b"") -> None:
                self._respond(status, {"error": {"message": message, "type": "scripted_probe_error"}}, body)

            def _handle(self) -> None:
                if ip_literal(self.client_address[0]) != IPV4_SERVER:
                    return self._error(403, "Only literal loopback peers are allowed")
                unknown_endpoint = self.command != "POST" or self.path != "/v1/chat/completions"
                lengths = self.headers.get_all("Content-Length") or []
                if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                    if unknown_endpoint:
                        return self._error(404, "Unknown scripted endpoint")
                    return self._error(400, "Exactly one Content-Length is required")
                try:
                    length = int(lengths[0])
                except ValueError:
                    return self._error(400, "Invalid Content-Length")
                if not 0 < length <= owner.max_request_bytes:
                    return self._error(413, "Request body exceeds scripted bounds")
                try:
                    body = self.rfile.read(length)
                except (socket.timeout, TimeoutError):
                    return self._error(408, "Request body timeout")
                if len(body) != length:
                    return self._error(400, "Incomplete request body", body)
                if unknown_endpoint:
                    return self._error(404, "Unknown scripted endpoint", body)
                try:
                    request = json.loads(body)
                except (ValueError, UnicodeError):
                    return self._error(400, "Request is not JSON", body)
                if not isinstance(request, dict) or request.get("stream", False) is not False:
                    return self._error(400, "Only nonstreaming JSON requests are supported", body)
                if not isinstance(request.get("messages"), list) or not isinstance(request.get("model"), str):
                    return self._error(400, "Request requires model and messages", body)
                with owner._lock:
                    exhausted = owner._position >= len(owner._script)
                    if not exhausted:
                        message = copy.deepcopy(owner._script[owner._position])
                        owner._position += 1
                        number = owner._position
                if exhausted:
                    return self._error(400, "Script exhausted; no fallback is permitted", body)
                payload = {
                    "id": f"h23-scripted-{number:04d}",
                    "object": "chat.completion",
                    "created": 0,
                    "model": request["model"],
                    "choices": [{
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
                self._respond(200, payload, body, script_number=number)

            do_POST = _handle
            do_GET = _handle
            do_PUT = _handle
            do_DELETE = _handle
            do_PATCH = _handle
            do_OPTIONS = _handle
            do_HEAD = _handle

        self._server = _NumericLoopbackHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release_withheld_response()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=self.request_timeout + 1)
        self._server = None
        self._thread = None
