"""Hermetic tests for KDSession's connect/break-in and resume-on-close logic.

A real kernel target needs a second machine, so these stand a fake in-process
"kd.exe" in front of the session: it emits the ``Connected to target`` banner the
way KDNET does, records the CTRL+BREAK used to halt the target, and echoes the
marker that proves the prompt is live. That exercises the real reader thread and
the real ``_startup`` handshake without a cable.

The genuine article is covered by ``scenarios/kernel_session.yaml``, which drives
a real kernel target when MCP_WINDBG_KERNEL_CONNECTION is set (see the local
coverage workflow in CLAUDE.md).
"""

from __future__ import annotations

import os
import queue
import signal

import pytest

from mcp_windbg import debug_session, kd_session
from mcp_windbg.kd_session import KDError, KDSession

_STOP = object()

_FAKE_KD = r"C:\fake\kd.exe"

# _startup breaks in with CTRL_BREAK_EVENT, which only exists on Windows.
windows_only = pytest.mark.skipif(
    os.name != "nt", reason="kernel break-in uses CTRL_BREAK_EVENT (Windows-only)"
)


class _FakeStdin:
    def __init__(self, proc):
        self._proc = proc
        self.writes: list[str] = []

    def write(self, text):
        self.writes.append(text)
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


class _FakeKd:
    """Minimal kd.exe stand-in.

    Args:
        connect: emit the ``Connected to target`` banner (as a live target does).
        answer_markers: echo ``.echo <marker>`` back, i.e. reach a prompt.
    """

    def __init__(self, *, connect: bool = True, answer_markers: bool = True):
        self._out: "queue.Queue" = queue.Queue()
        self._answer_markers = answer_markers
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        self.signals: list = []
        self.pid = 4321
        self._alive = True
        if connect:
            self._out.put(
                "Connected to target 172.16.2.189 on port 50005 on local IP 172.16.2.183."
            )

    def _feed(self, text: str):
        for line in text.splitlines():
            if line.startswith(".echo ") and self._answer_markers:
                self._out.put(line[len(".echo "):])

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, sig):
        self.signals.append(sig)

    def terminate(self):
        self._alive = False
        self._out.put(_STOP)

    def wait(self, timeout=None):
        self.terminate()
        return 0


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(kd_session.time, "sleep", lambda *_: None)


@pytest.fixture
def launch(monkeypatch):
    """Build a KDSession over a fake kd.exe; yields (session_factory, procs)."""
    procs: list[_FakeKd] = []
    launched: list[list[str]] = []

    def _factory(*, connect=True, answer_markers=True, **kwargs):
        proc = _FakeKd(connect=connect, answer_markers=answer_markers)
        procs.append(proc)

        def _popen(args, **_):
            launched.append(args)
            return proc

        # Pretend the fake kd.exe is on disk so find_executable resolves it.
        monkeypatch.setattr(debug_session.os.path, "isfile", lambda p: p == _FAKE_KD)
        monkeypatch.setattr(debug_session.subprocess, "Popen", _popen)
        kwargs.setdefault("kernel_connection", "net:port=50005,key=1.2.3.4")
        kwargs.setdefault("kd_path", _FAKE_KD)
        kwargs.setdefault("timeout", 5)
        return KDSession(**kwargs)

    yield _factory, procs, launched


@windows_only
def test_init_waits_for_banner_then_breaks_in(launch):
    factory, procs, _ = launch
    session = factory()
    # The banner set the connect gate, and we halted the running target.
    assert session._connected_event.is_set()
    assert procs[0].signals == [signal.CTRL_BREAK_EVENT]
    session.shutdown()


@windows_only
def test_init_launches_kd_with_dash_k(launch):
    factory, _, launched = launch
    session = factory(kernel_connection="net:port=50005,key=1.2.3.4")
    assert launched[0] == [_FAKE_KD, "-k", "net:port=50005,key=1.2.3.4"]
    session.shutdown()


@windows_only
def test_init_honors_custom_kd_path(launch):
    factory, _, _ = launch
    session = factory(kd_path=_FAKE_KD)
    assert session.kd_path == _FAKE_KD
    session.shutdown()


@windows_only
def test_startup_times_out_when_target_never_connects(launch):
    factory, _, _ = launch
    # No banner: kd is up but the target is not transmitting (kd's 'no_debuggee').
    with pytest.raises(KDError) as exc:
        factory(connect=False, timeout=1)
    assert "timed out waiting for the kernel target to connect" in str(exc.value).lower()


@windows_only
def test_startup_raises_when_prompt_never_arrives_after_break_in(launch):
    factory, _, _ = launch
    # Target connects, but the break-in never produces a prompt.
    with pytest.raises(KDError) as exc:
        factory(answer_markers=False, timeout=1)
    assert "did not reach a prompt" in str(exc.value).lower()


def test_init_without_connection_string_raises():
    with pytest.raises(ValueError, match="kernel_connection must be provided"):
        KDSession(kernel_connection="")


def test_init_raises_when_kd_not_found(monkeypatch):
    monkeypatch.setattr(debug_session.os.path, "isfile", lambda p: False)
    with pytest.raises(KDError, match="Could not find kd.exe"):
        KDSession(kernel_connection="net:port=50005,key=1.2.3.4")


def test_on_output_line_sets_connected_event_only_on_banner():
    obj = KDSession.__new__(KDSession)
    obj._connected_event = __import__("threading").Event()
    obj._on_output_line("Waiting to reconnect...")
    assert not obj._connected_event.is_set()
    obj._on_output_line("Connected to target 10.0.0.5 on port 50005 on local IP 10.0.0.1.")
    assert obj._connected_event.is_set()


# -- resume-on-close ------------------------------------------------------
#
# Exercised on an instance built with __new__ (bypassing the live connect), so
# these run anywhere.


class _ReleaseStdin:
    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)

    def flush(self):
        pass


class _ReleaseProc:
    def __init__(self):
        self.stdin = _ReleaseStdin()


def _kd_with_fake_process():
    obj = KDSession.__new__(KDSession)  # skip __init__ / live connect
    obj.process = _ReleaseProc()
    return obj


def test_release_target_resumes_with_g_by_default():
    obj = _kd_with_fake_process()
    obj.resume_on_close = True
    obj._release_target()
    writes = "".join(obj.process.stdin.writes)
    # It resumes the kernel target with 'g' (never a CTRL+B, which would not resume it).
    assert "g\n" in writes
    assert "\x02" not in writes


def test_release_target_leaves_halted_when_resume_false():
    obj = _kd_with_fake_process()
    obj.resume_on_close = False
    obj._release_target()
    assert obj.process.stdin.writes == []  # nothing sent -> stays at the break


def test_resume_on_close_defaults_true():
    assert KDSession.resume_on_close is True
