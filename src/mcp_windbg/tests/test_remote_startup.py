"""A remote client can echo markers before it has a usable thread context."""
from __future__ import annotations

import pytest

from mcp_windbg import cdb_session, debug_session
from mcp_windbg.cdb_session import CDBError, CDBSession
from test_debug_session import _FakeProc, _single_byte_code_page, _ctrl_break_event


class _RemoteProc(_FakeProc):
    def __init__(self, *, running=True, delay=0, signal_fails=False, no_context=False):
        super().__init__()
        self.running = running
        self.delay = delay
        self.signal_fails = signal_fails
        self.no_context = no_context
        self.probes = 0
        self.commands = []

    def _feed(self, text):
        # The remote client still interprets bare .echo while the target runs.
        for line in text.splitlines():
            self._handle(line)

    def _handle(self, line):
        self.commands.append(line)
        if line == "r":
            self.probes += 1
            if self.delay:
                self.delay -= 1
                self._out.put("WARNING: The debugger does not have a current thread")
            elif self.no_context or (self.running and not self.signals):
                self._out.put("WARNING: The debugger does not have a current process")
            else:
                self.running = False
                self._out.put("rax=0000000000000000 rbx=0000000000000000")
        else:
            super()._handle(line)

    def send_signal(self, signal):
        if self.signal_fails:
            raise OSError("break request rejected")
        self.signals.append(signal)


def _open(monkeypatch, proc, timeout=1):
    monkeypatch.setattr(cdb_session, "find_executable", lambda *args: "fake")
    monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *args, **kwargs: proc)
    return CDBSession(remote_connection="tcp:port=5005,server=localhost", timeout=timeout)


@pytest.mark.parametrize("running,delay", [(True, 0), (True, 2), (False, 0)])
def test_remote_open_waits_for_real_context(monkeypatch, running, delay):
    proc = _RemoteProc(running=running, delay=delay)
    session = _open(monkeypatch, proc)
    try:
        assert proc.signals
        assert proc.probes == delay + 1
        assert not proc.running
        assert not session._target_running
        assert session.send_command("!peb")[-1] == "OUT:!peb"
    finally:
        session.shutdown()
    assert not proc._alive
    session.reader_thread.join(timeout=1)
    assert not session.reader_thread.is_alive()


def test_missing_probe_marker_does_not_trigger_extra_timeout_recovery(monkeypatch):
    proc = _RemoteProc()
    proc.answer_budget = 1  # Answer the connection handshake, not the r probe.
    with pytest.raises(CDBError, match="Timed out waiting for debugger prompt"):
        _open(monkeypatch, proc, timeout=0.15)
    assert len(proc.signals) == 1
    assert not proc._alive


def test_a_failed_break_request_cleans_up(monkeypatch):
    proc = _RemoteProc(signal_fails=True)
    with pytest.raises(CDBError, match="break request rejected"):
        _open(monkeypatch, proc)
    assert not proc._alive
    assert proc.probes == 0


def test_markers_without_a_context_are_not_success(monkeypatch):
    proc = _RemoteProc(no_context=True)
    with pytest.raises(CDBError, match="did not provide a thread context") as failure:
        _open(monkeypatch, proc, timeout=0.15)
    assert "current process" in str(failure.value)
    assert not proc._alive
    assert "!peb" not in proc.commands


def test_remote_exit_does_not_become_a_context_timeout(monkeypatch):
    proc = _RemoteProc()
    proc.exit_with("failed to connect to remote server", code=5)
    with pytest.raises(CDBError, match="exit code 0x00000005"):
        _open(monkeypatch, proc)
    assert not proc._alive
    assert not proc.signals
