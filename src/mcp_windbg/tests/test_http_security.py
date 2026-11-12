"""HTTP transport request checks: DNS-rebinding protection and the bearer token.

Drives the real ASGI app from build_http_app (the same one serve_http runs) with
raw ASGI requests, so no HTTP client library or open port is needed.
"""

from __future__ import annotations

import json

import pytest

from mcp_windbg import server as server_module
from mcp_windbg.http_security import (
    build_transport_security,
    check_http_exposure,
    is_loopback_host,
)

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


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _post(app, host="127.0.0.1:8000", origin=None, authorization=None):
    headers = [
        (b"host", host.encode()),
        (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
    ]
    if origin:
        headers.append((b"origin", origin.encode()))
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    body = json.dumps(INITIALIZE).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp/",
        "raw_path": b"/mcp/",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status = {}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    await app(scope, receive, send)
    return status["code"]


async def _with_app(bind_host="127.0.0.1", allowed_hosts=None, auth_token=None, **request):
    server = server_module._create_server(transport="streamable-http")
    app = server_module.build_http_app(server, bind_host, allowed_hosts, auth_token)
    async with app.router.lifespan_context(app):
        return await _post(app, **request)


@pytest.mark.anyio
async def test_loopback_request_is_served():
    assert await _with_app() == 200


@pytest.mark.anyio
@pytest.mark.parametrize("host", ["localhost:8000", "[::1]:8000", "127.0.0.1"])
async def test_every_loopback_name_is_served(host):
    assert await _with_app(host=host) == 200


@pytest.mark.anyio
async def test_rebound_host_name_is_refused():
    # A DNS-rebinding page reaches 127.0.0.1 but sends its own name as Host.
    assert await _with_app(host="attacker.example:8000") == 421


@pytest.mark.anyio
async def test_foreign_origin_is_refused():
    assert await _with_app(origin="http://attacker.example") == 403


@pytest.mark.anyio
async def test_loopback_origin_is_served():
    assert await _with_app(origin="http://localhost:8000") == 200


@pytest.mark.anyio
async def test_allowed_host_is_served_on_wildcard_bind():
    assert await _with_app(bind_host="0.0.0.0", allowed_hosts=["debugbox"],
                           host="debugbox:8000") == 200
    assert await _with_app(bind_host="0.0.0.0", host="debugbox:8000") == 421


@pytest.mark.anyio
async def test_specific_bind_address_is_served():
    assert await _with_app(bind_host="10.0.0.5", host="10.0.0.5:8000") == 200


@pytest.mark.anyio
async def test_token_required_when_configured():
    assert await _with_app(auth_token="s3cret") == 401
    assert await _with_app(auth_token="s3cret", authorization="Bearer wrong") == 401
    assert await _with_app(auth_token="s3cret", authorization="s3cret") == 401
    assert await _with_app(auth_token="s3cret", authorization="Bearer s3cret") == 200


def test_allowed_host_with_port_pins_that_port():
    settings = build_transport_security("0.0.0.0", ["debugbox:9000"])
    assert "debugbox:9000" in settings.allowed_hosts
    assert "debugbox:*" not in settings.allowed_hosts
    assert settings.enable_dns_rebinding_protection


def test_wildcard_bind_does_not_allow_any_host():
    settings = build_transport_security("0.0.0.0")
    assert not any(h.startswith("0.0.0.0") for h in settings.allowed_hosts)


@pytest.mark.parametrize("host, loopback", [
    ("127.0.0.1", True), ("127.0.0.2", True), ("localhost", True), ("::1", True),
    ("[::1]", True), ("0.0.0.0", False), ("::", False), ("10.0.0.5", False),
    ("debugbox", False),
])
def test_is_loopback_host(host, loopback):
    assert is_loopback_host(host) is loopback


def test_exposed_bind_without_token_is_refused():
    with pytest.raises(SystemExit, match="MCP_WINDBG_AUTH_TOKEN"):
        check_http_exposure("0.0.0.0", None, False)


@pytest.mark.parametrize("host, token, allow", [
    ("127.0.0.1", None, False),
    ("0.0.0.0", "s3cret", False),
    ("0.0.0.0", None, True),
])
def test_exposure_check_passes(host, token, allow):
    check_http_exposure(host, token, allow)


def test_ipv6_allowed_host_is_bracketed():
    settings = build_transport_security("0.0.0.0", ["fe80::1"])
    assert "[fe80::1]:*" in settings.allowed_hosts


def test_empty_token_is_rejected():
    from mcp_windbg.http_security import BearerTokenMiddleware

    with pytest.raises(ValueError):
        BearerTokenMiddleware(lambda *a: None, "")


@pytest.mark.anyio
async def test_token_check_passes_non_http_scopes_through():
    from mcp_windbg.http_security import BearerTokenMiddleware

    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await BearerTokenMiddleware(inner, "s3cret")({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]
