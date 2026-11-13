"""The streamable-http transport answers tool calls over SSE, not plain JSON (#165).

A plain JSON response sends nothing until the tool finishes, and Claude Code
gives up on one after about 60 s. An SSE response starts at once.
This drives the session manager serve_http mounts, at the ASGI level.
"""

from __future__ import annotations

import json

import anyio
import pytest

from mcp_windbg import server as server_module


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _post(
    manager, body: dict, accept: bytes = b"application/json, text/event-stream"
) -> tuple[int, dict, bytes]:
    payload = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            (b"content-type", b"application/json"),
            (b"accept", accept),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await anyio.sleep_forever()

    headers: dict = {}
    chunks: list = []
    status = []

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])
            headers.update({k.decode().lower(): v.decode() for k, v in message["headers"]})
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    with anyio.fail_after(10):
        await manager.handle_request(scope, receive, send)
    return status[0], headers, b"".join(chunks)


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.mark.anyio
async def test_initialize_is_answered_over_sse(monkeypatch):
    monkeypatch.setattr(server_module, "_sessions", {})
    manager = server_module._http_session_manager(server_module._create_server())
    async with manager.run():
        status, headers, body = await _post(manager, INITIALIZE)
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert b"event: message" in body
    assert b"serverInfo" in body


@pytest.mark.anyio
async def test_a_json_only_client_is_told_what_to_accept(monkeypatch):
    """The Streamable HTTP spec requires clients to accept both types. One that
    does not is refused with a reason, not with a hang or an empty answer."""
    monkeypatch.setattr(server_module, "_sessions", {})
    manager = server_module._http_session_manager(server_module._create_server())
    async with manager.run():
        status, _, body = await _post(manager, INITIALIZE, accept=b"application/json")
    assert status == 406
    assert b"text/event-stream" in body
