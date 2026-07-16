"""Hermetic tests for the DebuggerSession marker protocol.

A fake in-process "debugger" stands in for cdb/kd: it reads the lines written to
stdin and, mimicking the real thing, emits ``OUT:<cmd>`` for a command line and
echoes the marker for a ``.echo <marker>`` line. That lets us exercise the real
reader thread, per-command markers, and the timeout path without a debugger and
without a live target.
"""

from __future__ import annotations

import queue

import pytest

from mcp_windbg import debug_session
from mcp_windbg.debug_session import DebuggerError, DebuggerSession

_STOP = object()


class _FakeStdin:
    def __init__(self, proc):
        self._proc = proc

    def write(self, text: str):
        self._proc._feed(text)

    def flush(self):
        pass


class _FakeStdout:
    """Blocking line iterator backed by a queue the fake process fills."""

    def __init__(self, proc):
        self._proc = proc

    def __iter__(self):
        return self

    def __next__(self):
        item = self._proc._out.get()
        if item is _STOP:
            raise StopIteration
        return item


class _FakeProc:
    """Minimal subprocess.Popen stand-in driven by what is written to stdin."""

    def __init__(self, *, swallow_markers: bool = False):
        self._out: "queue.Queue" = queue.Queue()
        self._swallow = swallow_markers
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        self._alive = True
        self.signals: list = []

    def _feed(self, text: str):
        for line in text.splitlines():
            if line.startswith(".echo "):
                marker = line[len(".echo "):]
                if not self._swallow:
                    self._out.put(marker)
            elif line in ("q", "\x02"):
                continue  # shutdown control lines produce no output
            else:
                self._out.put(f"OUT:{line}")

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, sig):
        self.signals.append(sig)

    def terminate(self):
        self._alive = False
        self._out.put(_STOP)

    def wait(self, timeout=None):
        return 0


class _Session(DebuggerSession):
    is_live_session = False


@pytest.fixture
def make_session(monkeypatch):
    """Build a session over a fake process. Markers always work during startup;
    the returned proc can be switched to swallow markers afterwards."""
    created = {}

    def _factory(timeout=5):
        proc = _FakeProc()
        monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *a, **k: proc)
        session = _Session(
            debugger_path="fake", launch_args=["fake"], timeout=timeout, verbose=False
        )
        created["session"] = session
        return session, proc

    yield _factory
    session = created.get("session")
    if session is not None:
        session.shutdown()


def test_startup_reaches_prompt(make_session):
    session, _ = make_session()
    # If _startup's marker handshake worked, the session is ready and marker seq
    # has advanced past the startup marker.
    assert session._marker_seq == 1


def test_sequential_commands_return_their_own_output(make_session):
    session, _ = make_session()
    assert session.send_command("first") == ["OUT:first"]
    assert session.send_command("second") == ["OUT:second"]
    assert session.send_command("third") == ["OUT:third"]


def test_markers_are_unique_per_command(make_session):
    session, _ = make_session()
    before = session._marker_seq
    session.send_command("a")
    session.send_command("b")
    # Two commands consumed two distinct, monotonically increasing markers.
    assert session._marker_seq == before + 2


def test_timeout_raises_when_marker_never_arrives(make_session):
    session, proc = make_session(timeout=1)
    proc._swallow = True  # from now on the fake never completes a command
    with pytest.raises(DebuggerError) as exc:
        session.send_command("hangs")
    assert "timed out" in str(exc.value).lower()
