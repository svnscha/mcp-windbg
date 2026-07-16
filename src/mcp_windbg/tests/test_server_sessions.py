"""Hermetic unit tests for the session registry and timeout resolution.

These cover the session-id bookkeeping the MCP layer does around a session
object, without needing a real debugger: kind-checking so run_cdb_command can't
drive a kernel session, unknown-id errors, close semantics, and the per-tool
timeout precedence.
"""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError

from mcp_windbg import server


class _FakeSession:
    """Stand-in for a CDB/KD session; records whether it was shut down."""

    def __init__(self, is_live: bool = False):
        self.is_live_session = is_live
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture(autouse=True)
def _clear_registry():
    server._sessions.clear()
    yield
    server._sessions.clear()


def test_register_returns_kind_prefixed_id():
    sid = server._register_session(_FakeSession(), "cdb", "dump x")
    assert sid.startswith("cdb-")
    assert server._sessions[sid]["kind"] == "cdb"


def test_ids_are_unique():
    a = server._register_session(_FakeSession(), "kd", "k1")
    b = server._register_session(_FakeSession(), "kd", "k2")
    assert a != b


def test_require_session_returns_session():
    s = _FakeSession()
    sid = server._register_session(s, "cdb", "dump")
    assert server._require_session(sid, "cdb") is s


def test_require_unknown_id_raises():
    with pytest.raises(McpError) as exc:
        server._require_session("cdb-deadbeef", "cdb")
    assert "Unknown session_id" in str(exc.value)


def test_require_wrong_kind_raises_with_guidance():
    sid = server._register_session(_FakeSession(), "kd", "kernel")
    with pytest.raises(McpError) as exc:
        server._require_session(sid, "cdb")  # using a cdb tool on a kd session
    message = str(exc.value)
    assert "is a kd session" in message
    assert "run_kd_command" in message


def test_close_shuts_down_and_forgets():
    s = _FakeSession()
    sid = server._register_session(s, "cdb", "dump")
    assert server._close_session(sid, "cdb") is True
    assert s.shutdown_called is True
    assert sid not in server._sessions


def test_close_wrong_kind_is_noop():
    sid = server._register_session(_FakeSession(), "kd", "kernel")
    assert server._close_session(sid, "cdb") is False
    assert sid in server._sessions  # still there, not closed by the wrong tool


def test_close_unknown_id_is_false():
    assert server._close_session("cdb-00000000", "cdb") is False


@pytest.mark.parametrize(
    "per_call, tool_default, server_timeout, expected",
    [
        (None, 60, 60, 60),      # default equals floor
        (None, 180, 60, 180),    # tool default wins over floor
        (None, 60, 120, 120),    # --timeout floor raises it
        (30, 180, 60, 30),       # explicit per-call override wins
        (0, 180, 60, 180),       # 0/falsey ignored -> default path
    ],
)
def test_effective_timeout(per_call, tool_default, server_timeout, expected):
    assert server._effective_timeout(per_call, tool_default, server_timeout) == expected
