"""Hermetic tests for DebuggerSession's timeout and go/break-in handling.

The scenarios drive a real debugger and cover the marker protocol's happy path
(a command returns its own output) far better than a fake can. What they cannot
do is make a debugger stop answering, or make a target bugcheck on cue. So the
fake in-process "debugger" here covers those: it can be told to swallow markers
(exercising the cancel-on-timeout resync), and it models the one behaviour that
makes go-class commands special - while the target runs, the debugger stops
reading its stdin, so anything written to it is queued until the target stops.
"""

from __future__ import annotations

import os
import queue
import threading
import time

import pytest

from mcp_windbg import debug_session
from mcp_windbg.debug_session import DebuggerError, DebuggerSession

_STOP = object()

# Commands the fake debugger recognizes as resuming the target.
_GO = ("g", "gh", "gn", "gN", "gc", "gu")


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
    """Minimal subprocess.Popen stand-in driven by what is written to stdin.

    Args:
        swallow_markers: never echo ``.echo <marker>`` back, i.e. never finish a
            command - used to force the timeout path.
        resumes_on_go: model a live target. A go-class command sets the process
            "running": from then on stdin is queued rather than processed, the
            way a real debugger stops reading input once the target has the CPU.
            Only :meth:`target_stops` (a bugcheck/breakpoint) or CTRL+BREAK
            drains that queue.
    """

    def __init__(
        self,
        *,
        swallow_markers: bool = False,
        resumes_on_go: bool = False,
        breaks_on_signal: bool = True,
    ):
        self._out: "queue.Queue" = queue.Queue()
        self._swallow = swallow_markers
        self._resumes_on_go = resumes_on_go
        self._breaks_on_signal = breaks_on_signal
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        self._alive = True
        self.pid = 4321
        self.signals: list = []
        self.running = False
        self._queued: list = []
        #: When set, answer this many more markers and swallow the rest. Lets a
        #: test hang one specific command without also hanging the break-in
        #: probe that precedes it.
        self.answer_budget: "int | None" = None
        #: When a ``.logopen /u <path>`` is seen, the fake mirrors a UTF-16
        #: transcript to this file the way cdb/kd do, so the log-content channel
        #: can be exercised without a real multibyte debugger.
        self._log_path = None

    def _feed(self, text: str):
        for line in text.splitlines():
            if self.running:
                self._queued.append(line)  # target has the CPU; stdin is not read
            else:
                self._handle(line)

    def _open_log(self, path: str):
        self._log_path = path
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe")  # UTF-16LE BOM, as cdb writes
        self._log_write("Opened log file\r\n")

    def _log_write(self, text: str):
        with open(self._log_path, "ab") as handle:
            handle.write(text.encode("utf-16-le"))

    def _handle(self, line: str):
        logging = self._log_path is not None and not line.startswith(".logopen")
        if logging:
            self._log_write(f"0:000> {line}\r\n")  # the transcript echoes the command
        if line.startswith(".echo "):
            marker = line[len(".echo "):]
            if self.answer_budget is not None:
                if self.answer_budget <= 0:
                    return
                self.answer_budget -= 1
            if not self._swallow:
                self._out.put(marker)
                if logging:
                    self._log_write(f"{marker}\r\n")
        elif line.startswith(".logopen /u "):
            self._open_log(line[len(".logopen /u "):].strip())
        elif line in ("q", "\x02"):
            # quit / detach: the real process exits, ending the reader loop
            self._alive = False
            self._out.put(_STOP)
        elif self._resumes_on_go and line.split()[:1] and line.split()[0] in _GO:
            self.running = True
        else:
            self._out.put(f"OUT:{line}")
            if logging:
                self._log_write(f"OUT:{line}\r\n")

    def target_stops(self, *lines: str):
        """The target halts on its own (bugcheck, breakpoint), draining stdin."""
        if not self.running:
            return
        self.running = False
        for line in lines:
            self._out.put(line)
        queued, self._queued = self._queued, []
        for line in queued:
            self._handle(line)

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, sig):
        self.signals.append(sig)
        if self._breaks_on_signal:
            self.target_stops("Break instruction exception - code 80000003 (first chance)")

    def terminate(self):
        self._alive = False
        self._out.put(_STOP)

    def wait(self, timeout=None):
        return 0


class _Session(DebuggerSession):
    is_live_session = False


class _LiveSession(DebuggerSession):
    is_live_session = True


@pytest.fixture(autouse=True)
def _fast_break_in_probe(monkeypatch):
    """_break_in_and_resync probes for a prompt before signalling. The real 2s
    is about a slow kernel cable; the fake answers instantly or not at all, so
    shorten it and keep the suite quick."""
    monkeypatch.setattr(debug_session, "BREAK_IN_PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr(debug_session, "RESUME_CONFIRM_TIMEOUT", 0.2)


@pytest.fixture(autouse=True)
def _single_byte_code_page(monkeypatch):
    """Default the hermetic tests to the single-byte (pipe) path, so they behave
    the same whatever the host's real code page is - otherwise a session built on
    a multibyte host would auto-open the Unicode log mid-test. The log tests
    re-patch this to True where they need it."""
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: False)


@pytest.fixture(autouse=True)
def _ctrl_break_event(monkeypatch):
    """CTRL_BREAK_EVENT only exists on Windows, and the fake process never
    delivers a real signal - so define it where it is missing and let the
    break-in paths be exercised on any platform."""
    monkeypatch.setattr(debug_session.signal, "CTRL_BREAK_EVENT", 21, raising=False)


@pytest.fixture
def make_session(monkeypatch):
    """Build a session over a fake process. Markers always work during startup;
    the returned proc can be switched to swallow markers afterwards."""
    created = []

    def _factory(timeout=5, live=False, breaks_on_signal=True):
        proc = _FakeProc(resumes_on_go=live, breaks_on_signal=breaks_on_signal)
        monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *a, **k: proc)
        cls = _LiveSession if live else _Session
        session = cls(
            debugger_path="fake", launch_args=["fake"], timeout=timeout, verbose=False
        )
        created.append(session)
        return session, proc

    yield _factory
    for session in created:
        try:
            session.shutdown()
        except Exception:
            pass


def test_timeout_raises_when_marker_never_arrives(make_session):
    session, proc = make_session(timeout=1)
    proc._swallow = True  # from now on the fake never completes a command
    with pytest.raises(DebuggerError) as exc:
        session.send_command("hangs")
    assert "timed out" in str(exc.value).lower()


# -- go-class commands ----------------------------------------------------
#
# The bug these cover: 'g' hands the CPU to the target, after which the debugger
# stops reading stdin. Marking the command would queue an .echo the debugger
# cannot answer, so send_command would "time out" and its cancel-on-timeout
# CTRL+BREAK would halt the very target 'g' had just released.


def test_go_command_returns_immediately_and_leaves_target_running(make_session):
    session, proc = make_session(live=True)
    out = session.send_command("g")
    assert proc.running is True
    assert session._target_running is True
    assert any("resumed" in line.lower() for line in out)
    # What is queued behind the go is the probe that confirms the debugger
    # consumed it, and nothing else. It is deliberately not a completion marker
    # the command waits on: this call returned with the target still running.
    assert proc._queued and all(line.startswith(".echo ") for line in proc._queued)


def test_go_command_with_an_argument_is_still_go(make_session):
    session, proc = make_session(live=True)
    session.send_command("g 0x7ffb1234")
    assert session._target_running is True


@pytest.mark.parametrize("command", ["p", "t", "pa", "ta", "pt", "tt", "gu_not_a_command"])
def test_step_commands_are_not_treated_as_go(make_session, command):
    """Stepping returns to the prompt on its own, so it must keep the marker
    round-trip - otherwise its output is swallowed and the session wrongly
    believes the target is running."""
    session, proc = make_session(live=True)
    out = session.send_command(command)
    assert out == [f"OUT:{command}"]
    assert session._target_running is False
    assert proc.running is False


def test_go_that_stops_at_once_reports_the_stop_not_a_resume(make_session):
    """A ``gu`` that returns in microseconds, or a breakpoint hit immediately,
    leaves the target back at a prompt. Reporting "target resumed" there is two
    lies for the price of one: it discards the output the caller wanted, and it
    leaves ``_target_running`` set so the next ordinary command breaks into a
    target that already stopped.

    Confirming the resume is what makes this distinguishable - the debugger
    answering the probe *is* the evidence the target never left.
    """
    session, proc = make_session(live=True)
    proc._resumes_on_go = False  # the target comes straight back to the prompt
    out = session.send_command("gu")
    assert session._target_running is False
    assert proc.running is False
    assert out == ["OUT:gu"]
    assert not any("resumed" in line.lower() for line in out)


def test_go_on_a_dump_session_uses_the_normal_marker_protocol(make_session):
    """A dump has no target to resume; 'g' there is just another command."""
    session, _ = make_session(live=False)
    assert session.send_command("g") == ["OUT:g"]
    assert session._target_running is False


def test_ordinary_command_breaks_in_first_when_the_target_is_running(make_session):
    session, proc = make_session(live=True)
    session.send_command("g")
    out = session.send_command("k")
    assert debug_session.signal.CTRL_BREAK_EVENT in proc.signals
    # Why the target stopped leads the output; it is not dropped on the floor.
    assert out == ["Break instruction exception - code 80000003 (first chance)", "OUT:k"]
    assert session._target_running is False


def test_break_in_probes_before_signalling_an_already_stopped_target(make_session):
    """A CTRL+BREAK aimed at a halted kernel target queues a break request that
    stops the machine again later, so the probe has to come first."""
    session, proc = make_session(live=True)
    session.send_command("g")
    proc.target_stops("Breakpoint 0 hit")  # stopped on its own; we do not know yet
    out = session.send_command("k")
    assert proc.signals == []  # no stray CTRL+BREAK
    assert out == ["Breakpoint 0 hit", "OUT:k"]
    assert session._target_running is False


def test_send_ctrl_break_does_not_assume_the_break_landed(make_session):
    """CTRL+BREAK only requests a break. The flag stays set and the next
    command's probe establishes the truth - one cheap round-trip, no lie."""
    session, proc = make_session(live=True)
    session.send_command("g")
    session.send_ctrl_break()
    assert session._target_running is True
    before = len(proc.signals)
    out = session.send_command("k")
    assert len(proc.signals) == before  # probe answered; no second signal
    assert out == ["Break instruction exception - code 80000003 (first chance)", "OUT:k"]
    assert session._target_running is False


def test_a_second_go_is_refused_while_the_target_is_running(make_session):
    """Writing it would queue a resume that fires the moment the target stops,
    leaving the session's view of the target wrong."""
    session, proc = make_session(live=True)
    session.send_command("g")
    out = session.send_command("g")
    assert any("already running" in line for line in out)
    # Only the probe's marker was queued - never a second resume, which would
    # have fired the instant the target stopped.
    assert [line for line in proc._queued if not line.startswith(".echo ")] == []
    assert session._target_running is True


def test_break_in_failure_leaves_no_marker_pending(make_session):
    """If CTRL+BREAK does not land, the abandoned marker must not surface later
    as a phantom completion for whatever runs next."""
    session, proc = make_session(timeout=3, live=True, breaks_on_signal=False)
    session.send_command("g")
    with pytest.raises(DebuggerError) as exc:
        session.send_command("k")
    assert "did not stop after CTRL+BREAK" in str(exc.value)
    assert session._expected_marker is None
    assert session._target_running is True

    # The target finally stops; the queued markers echo but are dropped as ours,
    # so the next command sees only real output.
    proc.target_stops("Breakpoint 0 hit")
    out = session.send_command("k")
    assert not any(debug_session.MARKER_BASE in line for line in out)
    assert out[-1] == "OUT:k"


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
