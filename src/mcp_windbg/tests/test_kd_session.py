"""Hermetic tests for KDSession's resume-on-close behavior.

We can't stand up a real kernel target here, so we exercise the release hook
directly on an instance built with __new__ (bypassing the live connect).
"""

from __future__ import annotations

import pytest

from mcp_windbg import kd_session
from mcp_windbg.kd_session import KDSession


class _FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(kd_session.time, "sleep", lambda *_: None)


def _kd_with_fake_process():
    obj = KDSession.__new__(KDSession)  # skip __init__ / live connect
    obj.process = _FakeProc()
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
