"""Synthetic transport checks; these never import or execute the ADK harness."""

import asyncio
import base64
import copy
import io
import json
import sys
import threading
from email.message import Message
from types import SimpleNamespace

import pytest

from tools.harness_cert._scripted_loopback import (
    ScriptedServer, _NumericLoopbackHTTPServer, create_client, validate_loopback_url,
)


@pytest.fixture
def in_memory_server(monkeypatch):
    """Exercise the real HTTP handler without requiring socket-bind permission."""
    class FakeHTTPServer:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
            self.server_port = 31337
            self.handler = handler

        def serve_forever(self):
            pass

        def shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr("tools.harness_cert._scripted_loopback._NumericLoopbackHTTPServer", FakeHTTPServer)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:1234/v1", "http://localhost:1234/v1", "http://127.0.0.2:1234/v1",
    "http://127.0.0.1/v1", "http://user:secret@127.0.0.1:1234/v1",
    "http://127.0.0.1:1234/v1?redirect=external", "http://127.0.0.1:1234/v1#fragment",
    "http://127.0.0.1:1234/v1/../other", "http://127.0.0.1:1234/v1/%2e%2e/other",
    "http://127.0.0.1:1234/v1//other", "http://127.0.0.1:0/v1",
    "http://127.0.0.1:1234/other", "http://127.0.0.1:1234/v1\n",
    "http://[::1]:1234/v1", "http://[0:0:0:0:0:0:0:1]:1234/v1",
    "http://[::1%lo0]:1234/v1", "http://[::1%25lo0]:1234/v1",
    "http://[::ffff:127.0.0.1]:1234/v1", "http://[::]:1234/v1",
    "http://[0:0:0:0:0:ffff:7f00:1]:1234/v1", "http://[::ffff:8.8.8.8]:1234/v1",
    "http://[fe80::1]:1234/v1", "http://[fc00::1]:1234/v1", "http://[2001:db8::1]:1234/v1",
    "http://[2001:4860:4860::8888]:1234/v1", "http://8.8.8.8:1234/v1",
    "http://0.0.0.0:1234/v1", "http://10.0.0.2:1234/v1",
    "http://203.0.113.1:1234/v1", "http://127.1:1234/v1",
    "http://2130706433:1234/v1", "http://127.000.000.001:1234/v1",
    "http://127.0.0.1.example.com:1234/v1", "http://127.0.0.1:1234@external.invalid/v1",
    "http://127.0.0.1.evil.example:1234/v1",
    "http://external.invalid@127.0.0.1:1234/v1", "http://127.0.0.1:1234\\@external.invalid/v1",
    "http://127.0.0.1:65536/v1", "http://127.0.0.1:-1/v1",
    "http://127.0.0.1:1234/v1/./chat/completions", "http://127.0.0.1:1234/v1/%2fexternal",
    "http://127.0.0.1:1234/v1?next=http://external.invalid", "//127.0.0.1:1234/v1",
])
def test_guard_rejects_nonliteral_or_escaping_urls(url):
    with pytest.raises(ValueError):
        validate_loopback_url(url)


def test_guard_requires_the_same_port_and_origin():
    assert validate_loopback_url("http://127.0.0.1:1234/v1/chat/completions", "http://127.0.0.1:1234")
    with pytest.raises(ValueError, match="leave"):
        validate_loopback_url("http://127.0.0.1:1235/v1/chat/completions", "http://127.0.0.1:1234")


@pytest.mark.parametrize("origin", [
    "http://[::1]:1234", "http://[::1%lo0]:1234", "http://[::ffff:127.0.0.1]:1234",
    "http://localhost:1234", "http://127.0.0.2:1234", "https://127.0.0.1:1234",
    "http://127.0.0.1:1234?redirect=external", "http://127.0.0.1:1234/v1",
])
def test_guard_refuses_non_ipv4_or_untrusted_expected_origin(origin):
    with pytest.raises(ValueError):
        validate_loopback_url("http://127.0.0.1:1234/v1/chat/completions", origin)


def _request(server, payload, path="/v1/chat/completions", method="POST", peer="127.0.0.1"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    handler = object.__new__(server._server.handler)
    handler.client_address = (peer, 31337)
    handler.command = method
    handler.path = path
    handler.request_version = "HTTP/1.1"
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.headers["Content-Type"] = "application/json"
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler._handle()
    headers, content = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    status = int(headers.split(b" ", 2)[1])
    assert int(dict(line.split(b": ", 1) for line in headers.split(b"\r\n")[1:])[b"Content-Length"]) == len(content)
    return status, json.loads(content)


def test_script_returns_tool_calls_and_final_text_and_records_exact_bodies(in_memory_server):
    tool_message = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call_local", "type": "function", "function": {"name": "local_tool", "arguments": "{}"},
    }]}
    with ScriptedServer() as server:
        assert server.base_url.startswith("http://127.0.0.1:")
        server.set_script([tool_message, {"role": "assistant", "content": "done"}])
        body = b'{"model":"h23-scripted","messages":[],"stream":false}'
        status, response = _request(server, body)
        assert status == 200
        assert response["choices"][0]["message"] == tool_message
        assert response["choices"][0]["finish_reason"] == "tool_calls"
        assert response["id"] == "h23-scripted-0001"
        assert base64.b64decode(server.calls[0]["request"]["body_base64"]) == body
        assert base64.b64decode(server.calls[0]["response"]["body_base64"]) == json.dumps(response, separators=(",", ":")).encode()
        status, response = _request(server, body)
        assert status == 200
        assert response["choices"][0]["message"]["content"] == "done"
        status, response = _request(server, body)
        assert status == 400
        assert "exhausted" in response["error"]["message"]
        assert len(server.calls) == 3
        json.dumps(server.calls)
    with pytest.raises(RuntimeError, match="not running"):
        _ = server.origin


def test_invalid_requests_fail_closed_without_consuming_the_script(in_memory_server):
    with ScriptedServer(max_request_bytes=128) as server:
        server.set_script([{"role": "assistant", "content": "local"}])
        request = {"model": "local", "messages": []}
        assert _request(server, request, path="/v1/models")[0] == 404
        assert _request(server, request, method="GET")[0] == 404
        assert _request(server, {**request, "stream": True})[0] == 400
        assert _request(server, b"not-json")[0] == 400
        assert _request(server, b"x" * 129)[0] == 413
        assert _request(server, request)[0] == 200
        assert [call["response"]["status"] for call in server.calls] == [404, 404, 400, 400, 413, 200]
        assert server.calls[0]["request"]["json"] == request
        assert server.calls[0]["request"]["body_complete"] is True
        assert server.calls[4]["request"]["body_complete"] is False


def _pending_handler(server):
    handler = object.__new__(server._server.handler)
    handler.client_address = ("127.0.0.1", 31337)
    handler.command, handler.path, handler.request_version = "POST", "/v1/chat/completions", "HTTP/1.1"
    body = b'{"model":"local","messages":[]}'
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    return handler


def test_withheld_request_is_recorded_before_release_and_never_sent(in_memory_server):
    with ScriptedServer() as server:
        server.set_script([{"role": "assistant", "content": "held"}])
        server.withhold_response(1)
        handler = _pending_handler(server)
        worker = threading.Thread(target=handler._handle)
        worker.start()
        try:
            assert server._held_admitted.wait(timeout=1)
            assert server.calls[0]["script_number"] == 1
            assert server.calls[0]["request"]["body_complete"]
            assert server.calls[0]["response"]["delivery"] == "withheld"
            assert handler.wfile.getvalue() == b""
            with pytest.raises(RuntimeError, match="has not finished"):
                server.set_script([])
            assert server.release_withheld_response()
        finally:
            server.release_withheld_response()
            worker.join(timeout=1)
        assert not worker.is_alive()
        assert server.calls[0]["response"]["delivery"] == "discarded_after_release"
        assert handler.wfile.getvalue() == b""
        assert handler.close_connection
        server.set_script([{"role": "assistant", "content": "normal"}])
        assert _request(server, {"model": "local", "messages": []})[0] == 200
        assert server.calls[-1]["response"]["delivery"] == "sent"


def test_withheld_response_wait_is_bounded_and_recorded_as_failure(in_memory_server):
    with ScriptedServer() as server:
        server.set_script([{"role": "assistant", "content": "held"}])
        server.withhold_response(1, max_wait=0.001)
        handler = _pending_handler(server)
        handler._handle()
        assert server.calls[0]["response"]["delivery"] == "hold_bound_expired"
        assert handler.wfile.getvalue() == b""


@pytest.mark.parametrize("number,wait", [(0, 1), (2, 1), (True, 1), (1, 0), (1, 9), (1, float("nan"))])
def test_withholding_refuses_unknown_response_or_unbounded_wait(number, wait):
    server = ScriptedServer()
    server.set_script([{"role": "assistant", "content": "held"}])
    with pytest.raises(ValueError):
        server.withhold_response(number, max_wait=wait)


@pytest.mark.parametrize("peer", [
    "localhost", "127.0.0.2", "127.1", "0.0.0.0", "10.0.0.2", "203.0.113.1",
    "::1", "0:0:0:0:0:0:0:1", "::1%lo0", "::ffff:127.0.0.1", "::",
])
def test_scripted_handler_refuses_non_ipv4_peers_without_consuming_script(in_memory_server, peer):
    with ScriptedServer() as server:
        server.set_script([{"role": "assistant", "content": "local"}])
        request = {"model": "local", "messages": []}
        assert _request(server, request, peer=peer)[0] == 403
        assert _request(server, request)[0] == 200
        assert [call["response"]["status"] for call in server.calls] == [403, 200]


@pytest.mark.parametrize("kwargs", [{"request_timeout": float("inf")}, {"request_timeout": float("nan")}, {"max_request_bytes": 0}])
def test_server_rejects_unbounded_configuration(kwargs):
    with pytest.raises(ValueError):
        ScriptedServer(**kwargs)


def test_client_configuration_and_guard_with_fake_optional_dependencies(monkeypatch):
    created = {}
    monkeypatch.setenv("HTTP_PROXY", "http://external.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://external.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "socks5://external.invalid:1080")

    class FakeHttpClient:
        def __init__(self, **kwargs):
            created["http"] = kwargs

        async def aclose(self):
            created["closed"] = True

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created["sdk"] = kwargs
            self.http_client = kwargs["http_client"]
            self._platform = None

        async def request(self):
            if self._platform is None:
                pytest.fail("Optional SDK platform discovery would launch uname with DEVNULL")
            return {"X-Stainless-OS": self._platform}

        async def close(self):
            await self.http_client.aclose()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=FakeHttpClient, Timeout=lambda value: value))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeOpenAI))
    client, http_client = asyncio.run(create_client("http://127.0.0.1:1234/v1"))
    assert isinstance(client, FakeOpenAI)
    assert created["sdk"]["http_client"] is http_client
    assert created["sdk"]["max_retries"] == 0
    assert created["sdk"]["api_key"] == "H23_SCRIPTED_DUMMY"
    assert created["http"]["trust_env"] is False
    assert created["http"]["follow_redirects"] is False
    assert created["http"]["timeout"] == 10.0
    assert copy.deepcopy(client) is client
    assert asyncio.run(client.request()) == {"X-Stainless-OS": "Unknown"}
    assert asyncio.run(copy.deepcopy(client).request()) == {"X-Stainless-OS": "Unknown"}
    assert http_client.probe_guard_violations == []
    guard = created["http"]["event_hooks"]["request"][0]
    asyncio.run(guard(SimpleNamespace(url="http://127.0.0.1:1234/v1/chat/completions")))
    with pytest.raises(ValueError):
        asyncio.run(guard(SimpleNamespace(url="http://127.0.0.1:1235/v1/chat/completions")))
    with pytest.raises(ValueError):
        asyncio.run(guard(SimpleNamespace(url="https://external.invalid/v1/chat/completions")))
    assert len(http_client.probe_guard_violations) == 2
    assert "leave" in http_client.probe_guard_violations[0]
    assert "credentials" in http_client.probe_guard_violations[1]
    forbidden_requests = [
        "http://[::1]:1234/v1/chat/completions",
        "http://[::1%lo0]:1234/v1/chat/completions",
        "http://[::ffff:127.0.0.1]:1234/v1/chat/completions",
        "http://127.0.0.1:1234/v1/chat/completions?redirect=https://external.invalid",
        "http://127.0.0.1:1234/v1/../external",
        "http://127.0.0.1:1234@external.invalid/v1/chat/completions",
    ]
    for url in forbidden_requests:
        with pytest.raises(ValueError):
            asyncio.run(guard(SimpleNamespace(url=url)))
    assert len(http_client.probe_guard_violations) == 2 + len(forbidden_requests)
    asyncio.run(client.close())
    assert created["closed"] is True


def test_client_rejects_unsafe_url_before_importing_optional_packages(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ValueError):
        asyncio.run(create_client("https://external.invalid/v1"))


def test_numeric_bind_never_resolves_hostnames(monkeypatch):
    bound = []
    monkeypatch.setattr("tools.harness_cert._scripted_loopback.TCPServer.server_bind", lambda server: bound.append(server))

    def refuse_dns(*args, **kwargs):
        pytest.fail("Numeric listener must not perform DNS lookup")

    monkeypatch.setattr("socket.getfqdn", refuse_dns)
    monkeypatch.setattr("socket.gethostbyaddr", refuse_dns)
    server = object.__new__(_NumericLoopbackHTTPServer)
    server.socket = SimpleNamespace(getsockname=lambda: ("127.0.0.1", 31337))
    server.server_bind()
    assert bound == [server]
    assert server.server_name == "127.0.0.1"
    assert server.server_port == 31337
