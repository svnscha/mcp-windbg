"""Hermetic tests for how a -remote client's startup reads the target's state.

The remote scenarios attach to a stopped target, and one attaches to a running
one. What they cannot do is make the connect hang, or make cdb exit mid-connect
on cue. The fake here models the one thing that makes a remote open different
from a dump open: the client prints a connect banner, and after that a stopped
target answers a prompt probe while a running one does not.
"""

from __future__ import annotations

import pytest

from mcp_windbg import cdb_session, debug_session
from mcp_windbg.cdb_session import CDBError, CDBSession, REMOTE_CONNECTED_BANNER
from mcp_windbg.debug_session import DebuggerExitedError
from test_debug_session import _FakeProc, _ctrl_break_event, _fast_break_in_probe, _single_byte_code_page  # noqa: F401


class _RemoteProc(_FakeProc):
    """A cdb -remote client. ``running`` is the server's target state at the
    moment we attach; ``banner`` is whether the connect ever completes."""

    def __init__(self, *, running: bool, banner: bool = True):
        super().__init__(resumes_on_go=True)
        if banner:
            self._out.put(f"{REMOTE_CONNECTED_BANNER} 'tcp:Port=5005,Server=127.0.0.1'")
        self.running = running


@pytest.fixture(autouse=True)
def _fast_remote_probe(monkeypatch):
    monkeypatch.setattr(cdb_session, "REMOTE_PROMPT_PROBE_TIMEOUT", 0.2)


@pytest.fixture
def open_remote(monkeypatch):
    created = []

    def _factory(proc, timeout=5):
        monkeypatch.setattr(cdb_session, "find_executable", lambda *a: "fake")
        monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *a, **k: proc)
        session = CDBSession(remote_connection="tcp:Port=5005,Server=127.0.0.1", timeout=timeout)
        created.append(session)
        return session

    yield _factory
    for session in created:
        session.shutdown()


def test_a_stopped_target_opens_at_its_prompt(open_remote):
    proc = _RemoteProc(running=False)
    session = open_remote(proc)
    assert not session.target_running
    assert not proc.signals
    assert session.send_command("r") == ["OUT:r"]


def test_a_running_target_opens_running_and_the_first_command_breaks_in(open_remote):
    proc = _RemoteProc(running=True)
    session = open_remote(proc, timeout=60)  # must not wait anywhere near this
    assert session.target_running
    assert not proc.signals  # startup asked; it did not signal

    output = session.send_command("r")
    assert proc.signals == [debug_session.signal.CTRL_BREAK_EVENT]
    # The stop banner leads the reply; the startup probe's stray marker, which
    # the server answers once the target stops, is not published as output.
    assert output == ["Break instruction exception - code 80000003 (first chance)", "OUT:r"]
    assert not session.target_running


def test_a_failed_connect_reports_the_exit_not_a_timeout(open_remote):
    proc = _RemoteProc(running=False, banner=False)
    proc.exit_with("DebugConnect failed, Win32 error 0n10060", code=0x8007274C)
    with pytest.raises(DebuggerExitedError, match="0x8007274C") as failure:
        open_remote(proc, timeout=60)
    assert "DebugConnect failed" in str(failure.value)
    assert not proc.signals


def test_a_connect_that_never_completes_times_out_as_a_connect(open_remote):
    proc = _RemoteProc(running=False, banner=False)
    with pytest.raises(CDBError, match="connecting to the debug server"):
        open_remote(proc, timeout=0.3)
    assert not proc._alive  # shut down, not leaked
