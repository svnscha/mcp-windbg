"""Hermetic test for the DebuggerSession command timeout.

The scenarios drive a real debugger and cover the marker protocol's happy path
(a command returns its own output) far better than a fake can. What they cannot
do is make a debugger stop answering: hanging a real cdb.exe on demand is not
something a scenario can arrange. So the fake in-process "debugger" here exists
for that one case - it can be told to swallow markers, leaving the command to
time out and exercise the cancel-on-timeout resync.
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
        self.pid = 4321
        self.signals: list = []

    def _feed(self, text: str):
        for line in text.splitlines():
            if line.startswith(".echo "):
                marker = line[len(".echo "):]
                if not self._swallow:
                    self._out.put(marker)
            elif line in ("q", "\x02"):
                # quit / detach: the real process exits, ending the reader loop
                self._alive = False
                self._out.put(_STOP)
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


def test_timeout_raises_when_marker_never_arrives(make_session):
    session, proc = make_session(timeout=1)
    proc._swallow = True  # from now on the fake never completes a command
    with pytest.raises(DebuggerError) as exc:
        session.send_command("hangs")
    assert "timed out" in str(exc.value).lower()
