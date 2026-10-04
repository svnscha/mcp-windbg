"""Session control, concurrency and Unicode transport regressions."""
import os
import threading
import time
import pytest
from mcp_windbg import debug_session
from mcp_windbg.debug_session import DebuggerError, DebuggerExitedError, DebuggerSession
from test_debug_session import (
    _FakeProc, make_session, _fast_break_in_probe,
    _single_byte_code_page, _ctrl_break_event,
)

# -- wait_for_break -------------------------------------------------------


def test_wait_for_break_returns_what_the_target_printed_when_it_stopped(make_session):
    session, proc = make_session(live=True)
    session.send_command("g")
    timer = threading.Timer(
        0.05, lambda: proc.target_stops("*** Fatal System Error: 0x0000007e")
    )
    timer.start()
    try:
        out = session.wait_for_break(timeout=5)
    finally:
        timer.cancel()
    assert any("Fatal System Error" in line for line in out)
    assert session._target_running is False


def test_wait_for_break_says_so_when_the_target_is_already_stopped(make_session):
    session, _ = make_session(live=True)
    out = session.wait_for_break(timeout=5)
    assert out == ["Target was already stopped; there was nothing to wait for."]


def test_wait_for_break_asks_the_debugger_not_its_own_flag(make_session):
    """A target can be running for reasons this session never caused - resumed
    from the target console, or already going when the session was opened. The
    marker coming back is the proof it stopped; the flag is not."""
    session, proc = make_session(live=True)
    proc.running = True  # running, but _target_running was never set
    assert session._target_running is False
    timer = threading.Timer(0.05, lambda: proc.target_stops("*** Fatal System Error"))
    timer.start()
    try:
        out = session.wait_for_break(timeout=5)
    finally:
        timer.cancel()
    assert any("Fatal System Error" in line for line in out)


def test_wait_for_break_times_out_and_leaves_the_target_running(make_session):
    session, proc = make_session(live=True)
    session.send_command("g")
    with pytest.raises(DebuggerError) as exc:
        session.wait_for_break(timeout=1)
    assert "still running" in str(exc.value)
    # Still running, so the caller can wait again or break in.
    assert session._target_running is True
    assert proc.running is True


def test_wait_for_break_after_a_timeout_still_reports_the_stop(make_session):
    """The abandoned marker echoes harmlessly; the second wait gets the stop."""
    session, proc = make_session(live=True)
    session.send_command("g")
    with pytest.raises(DebuggerError):
        session.wait_for_break(timeout=1)
    timer = threading.Timer(0.05, lambda: proc.target_stops("Breakpoint 0 hit"))
    timer.start()
    try:
        out = session.wait_for_break(timeout=5)
    finally:
        timer.cancel()
    assert any("Breakpoint 0 hit" in line for line in out)
    # The marker abandoned by the first wait echoed on the way past; it is ours,
    # not the target's, and must not be reported as debugger output.
    assert not any(debug_session.MARKER_BASE in line for line in out)


# -- go classification ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "g",
        "  g  ",
        "g 0x7ffb1234",
        "gh",
        "gn",
        "gN",
        "gc",
        "gu",
        "~0 g",          # thread-qualified
        "~*g",
        "bp nt!NtCreateFile; g",   # the usual set-a-breakpoint-and-continue
        "g; k",
    ],
)
def test_is_go_command_recognizes_the_forms_callers_actually_write(command):
    assert DebuggerSession._is_go_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "",
        "   ",
        "k",
        "p", "t", "pa", "ta", "pt", "tt",   # stepping returns to the prompt
        "~",
        "|1s",
        ".echo gone",
        "bp g",          # a breakpoint on a symbol named g
        "!analyze -v",
        "lm m nt",
    ],
)
def test_is_go_command_rejects_everything_else(command):
    assert DebuggerSession._is_go_command(command) is False


# -- concurrency and lost-output regressions ------------------------------
#
# wait_for_break parks for minutes on a worker thread (the server runs it via
# anyio.to_thread so the event loop keeps serving), which is the first time two
# operations can genuinely overlap on one session.


def test_a_command_during_a_parked_wait_is_refused_not_interleaved(make_session):
    """Interleaving would let each operation install its marker over the
    other's, and both would return the wrong output - silently."""
    session, proc = make_session(live=True)
    session.send_command("g")

    errors: list = []
    started = threading.Event()

    def _wait():
        started.set()
        try:
            session.wait_for_break(timeout=2)
        except DebuggerError:
            pass

    waiter = threading.Thread(target=_wait, daemon=True)
    waiter.start()
    started.wait()
    time.sleep(0.2)  # let the waiter get past acquiring the session
    began = time.monotonic()
    try:
        session.send_command("k", timeout=60)
    except DebuggerError as exc:
        errors.append(str(exc))
    elapsed = time.monotonic() - began
    waiter.join(10)

    assert errors and "busy" in errors[0]
    assert "send_ctrl_break" in errors[0]
    # It must fail fast, not sit out its own 60s timeout: this call is made from
    # the server's event loop, so waiting here would stall every other session -
    # and the send_ctrl_break the message recommends.
    assert elapsed < 1


def test_output_landing_at_the_deadline_is_not_thrown_away(make_session, monkeypatch):
    """The bugcheck banner is the most valuable thing a kernel session ever
    prints; it must not be lost for arriving a microsecond after the deadline."""
    session, proc = make_session(live=True)
    session.send_command("g")

    real_wait = session.ready_event.wait

    def _wait_then_deliver(timeout=None):
        # Report the deadline as missed, but only after the target has in fact
        # stopped and the reader has published - the exact race being guarded.
        real_wait(0.05)
        proc.target_stops("*** Fatal System Error: 0x0000007e")
        real_wait(0.5)
        return False

    monkeypatch.setattr(session.ready_event, "wait", _wait_then_deliver)
    out = session.wait_for_break(timeout=1)
    assert any("Fatal System Error" in line for line in out)


def test_a_timed_out_command_still_reports_why_the_target_stopped(make_session):
    session, proc = make_session(timeout=3, live=True)
    session.send_command("g")
    proc.target_stops("*** Fatal System Error: 0x0000007e")
    # Answer the break-in probe, then hang: only the command times out.
    proc.answer_budget = 1
    with pytest.raises(DebuggerError) as exc:
        session.send_command("!analyze -v", timeout=1)
    assert "Fatal System Error" in str(exc.value)


def test_resume_after_ctrl_break_is_allowed(make_session):
    """send_ctrl_break leaves _target_running set on purpose; that must not make
    the resume it tells you about impossible."""
    session, proc = make_session(live=True)
    session.send_command("g")
    session.send_ctrl_break()
    out = session.send_command("g")
    assert any("resumed" in line.lower() for line in out)
    assert proc.running is True


def test_a_breakpoint_before_a_go_reports_whether_it_was_set(make_session):
    """'bp X; g' is one line to the caller but two things to the session: the
    breakpoint's result is the difference between a wait that can end and one
    that cannot."""
    session, proc = make_session(live=True)
    out = session.send_command("bp nt!NtCreateFile; g")
    assert "OUT:bp nt!NtCreateFile" in out
    assert any("resumed" in line.lower() for line in out)
    assert proc.running is True


@pytest.mark.parametrize(
    "command",
    [
        'bp nt!NtCreateFile ".echo hit; g;"',   # the '; g' is the breakpoint's own
        'bp X "kb; g "',
        '.echo "a; gc b"',
    ],
)
def test_a_go_inside_a_quoted_command_string_is_not_a_resume(command):
    assert DebuggerSession._is_go_command(command) is False


def test_closing_a_session_ends_a_parked_wait(make_session):
    """Otherwise a worker thread sits on a dead debugger for the rest of its
    timeout, and the caller is told the target is still running."""
    session, proc = make_session(live=True)
    session.send_command("g")

    outcome: list = []
    started = threading.Event()

    def _wait():
        started.set()
        try:
            outcome.append(session.wait_for_break(timeout=30))
        except DebuggerError as exc:
            outcome.append(exc)

    waiter = threading.Thread(target=_wait, daemon=True)
    waiter.start()
    started.wait()
    time.sleep(0.2)
    began = time.monotonic()
    session.shutdown()
    waiter.join(10)
    assert time.monotonic() - began < 5
    assert isinstance(outcome[0], DebuggerError)
    assert "closed" in str(outcome[0])


def test_resume_after_ctrl_break_reports_why_the_target_had_stopped(make_session):
    """The break banner must ride out on the resume, not be stranded in the
    session for the next operation to wipe."""
    session, proc = make_session(live=True)
    session.send_command("g")
    session.send_ctrl_break()
    out = session.send_command("g")
    assert any("Break instruction exception" in line for line in out)
    assert any("resumed" in line.lower() for line in out)


def test_a_timed_out_prefix_still_reports_why_the_target_stopped(make_session):
    """The 'bp X; g' path has its own preamble to lose."""
    session, proc = make_session(timeout=3, live=True)
    session.send_command("g")
    proc.target_stops("*** Fatal System Error: 0x0000007e")
    proc.answer_budget = 1  # answer the break-in probe, then hang
    with pytest.raises(DebuggerError) as exc:
        session.send_command("bp nt!NtCreateFile; g", timeout=1)
    assert "Fatal System Error" in str(exc.value)


def test_output_is_read_from_the_unicode_log_on_a_multibyte_code_page(make_session, monkeypatch):
    """On a multibyte code page the session opens a UTF-16 log and returns each
    command's output from it (the fake mirrors that log). The pipe still drives
    the markers; only the returned content comes from the log."""
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: True)
    session, proc = make_session()

    assert session._log_active is True
    assert proc._log_path is not None and os.path.exists(proc._log_path)
    # Content comes from the log, marker-synced, with the prompt/echo scaffolding
    # stripped - the same lines the pipe path would have returned.
    assert session.send_command("r rip") == ["OUT:r rip"]
    assert session.send_command("du @rsp") == ["OUT:du @rsp"]

    logpath = session._log_path
    session.shutdown()
    assert not os.path.exists(logpath)  # the temp log is cleaned up


def test_a_missing_log_falls_back_to_the_pipe_without_raising(make_session, monkeypatch):
    """If the log cannot be read, the command still returns (from the pipe)
    rather than failing - the reader must never depend on the log existing."""
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: True)
    session, proc = make_session()
    assert session._log_active is True

    # Drop the log out from under the reader: the next command falls back.
    os.remove(session._log_path)
    proc._log_path = None
    assert session.send_command("lm") == ["OUT:lm"]


def test_the_log_is_left_untouched_on_a_single_byte_code_page(make_session, monkeypatch):
    """The default (single-byte) path never opens a log and returns pipe output
    verbatim, so a Western setup is byte-for-byte unchanged."""
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: False)
    session, proc = make_session()

    assert session._log_active is False
    assert proc._log_path is None
    assert session.send_command("r rip") == ["OUT:r rip"]


def test_a_remote_client_keeps_the_pipe_even_on_a_multibyte_code_page(monkeypatch):
    """A -remote client's engine runs on the server, so the log would open there,
    not here. Such a session must stay on the pipe even on a multibyte page."""
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: True)

    class _RemoteLike(DebuggerSession):
        is_live_session = True
        _engine_is_local = False

    proc = _FakeProc()
    monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *a, **k: proc)
    session = _RemoteLike(debugger_path="fake", launch_args=["fake"], timeout=5, verbose=False)
    try:
        assert session._log_active is False
        assert proc._log_path is None
        assert session.send_command("r rip") == ["OUT:r rip"]
    finally:
        session.shutdown()
