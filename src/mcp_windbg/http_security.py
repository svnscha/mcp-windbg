"""Request checks for the streamable-http transport.

Two independent protections:

- **DNS-rebinding protection.** A web page the user visits can point its own
  host name at ``127.0.0.1`` and so reach a server bound to localhost from the
  browser. The request then carries the page's host name in ``Host`` (and its
  origin in ``Origin``), so the server only accepts requests whose ``Host`` is
  one it actually serves. The MCP SDK implements the check; it is off unless a
  ``TransportSecuritySettings`` is passed, which is what this module builds.
- **Bearer-token authentication** (``--auth-token`` / ``MCP_WINDBG_AUTH_TOKEN``).
  Required when the server binds a non-loopback address, unless explicitly
  waived with ``--allow-unauthenticated``.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from typing import Iterable, List, Optional

from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

AUTH_TOKEN_ENV = "MCP_WINDBG_AUTH_TOKEN"

_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")


def is_loopback_host(host: str) -> bool:
    """True if *host* (a bind address or host name) only accepts local connections."""
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _is_wildcard_bind(host: str) -> bool:
    return host.strip().strip("[]") in ("0.0.0.0", "::", "")


def _host_pattern(host: str) -> str:
    """``Host`` header pattern for *host* on any port (IPv6 literals bracketed)."""
    name = host.strip()
    if ":" in name and not name.startswith("["):
        name = f"[{name}]"
    return f"{name}:*"


def build_transport_security(
    bind_host: str, allowed_hosts: Optional[Iterable[str]] = None
) -> TransportSecuritySettings:
    """Host/Origin validation for a server bound to *bind_host*.

    Always accepts the loopback names. A specific bind address is accepted too;
    a wildcard bind (``0.0.0.0``, ``::``) is not, because the names other
    machines use to reach it cannot be known - list them with ``--allowed-host``.
    An entry may carry a port (``host:8000``) or not, in which case any port is
    accepted.
    """
    names: List[str] = list(_LOOPBACK_NAMES)
    if not _is_wildcard_bind(bind_host):
        names.append(bind_host)  # also a loopback address other than 127.0.0.1
    names.extend(h.strip() for h in (allowed_hosts or []) if h and h.strip())

    hosts: List[str] = []
    origins: List[str] = []
    for name in names:
        bare = name.strip()
        has_port = bare.rsplit(":", 1)[-1].isdigit() and (
            bare.count(":") == 1 or bare.startswith("[") and "]:" in bare
        )
        if has_port:
            hosts.append(bare)
            origins.extend([f"http://{bare}", f"https://{bare}"])
        else:
            pattern = _host_pattern(bare)
            hosts.extend([pattern, pattern[:-2]])
            origins.extend([f"http://{pattern}", f"https://{pattern}",
                            f"http://{pattern[:-2]}", f"https://{pattern[:-2]}"])

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


def resolve_auth_token(cli_value: Optional[str], environ=os.environ) -> Optional[str]:
    """The bearer token from ``--auth-token`` or the environment, without stray whitespace.

    A token never matches with surrounding whitespace, because the
    ``Authorization`` header is stripped before the comparison. cmd's
    ``set TOKEN=abc && ...`` stores ``"abc "``, and every request then failed
    with 401. A value that is only whitespace counts as no token.
    """
    raw = cli_value or environ.get(AUTH_TOKEN_ENV) or ""
    return raw.strip() or None


class BearerTokenMiddleware:
    """Reject HTTP requests that do not carry ``Authorization: Bearer <token>``."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("token must be non-empty")
        self.app = app
        self._expected = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = b""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                # The scheme is case-insensitive (RFC 9110), and the token may be
                # set off by more than one space.
                scheme, _, credentials = value.strip().partition(b" ")
                if scheme.lower() == b"bearer":
                    supplied = credentials.strip()
                break
        if not hmac.compare_digest(supplied, self._expected):
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"www-authenticate", b'Bearer realm="mcp-windbg"'),
                ],
            })
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self.app(scope, receive, send)


def check_http_exposure(host: str, auth_token: Optional[str], allow_unauthenticated: bool) -> None:
    """Refuse to serve a non-loopback address with no authentication.

    Anyone who can reach the port can run debugger commands on this machine, so
    an exposed, unauthenticated server is only started when the operator says
    so explicitly.
    """
    if is_loopback_host(host) or auth_token or allow_unauthenticated:
        return
    raise SystemExit(
        f"Refusing to bind {host} without authentication: anyone who can reach the "
        f"port could run debugger commands on this machine. Set {AUTH_TOKEN_ENV} "
        f"(or pass --auth-token), or pass --allow-unauthenticated if the network is "
        f"trusted."
    )
