"""Debugger safety regressions: fake processes only; never launch CDB/KD or taskkill."""
from __future__ import annotations

import sys
import logging
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import anyio
import pytest
from mcp.types import CallToolRequestParams

from mcp_windbg import cdb_session, debug_session, debug_process, server as server_module
from mcp_windbg.cdb_session import CDBSession
from test_debug_session import (
    _FakeProc, _ctrl_break_event, _single_byte_code_page, _fast_break_in_probe, make_session,
)
from test_open_cleanup import anyio_backend, open_handler

REAL_TERMINATE = debug_process.DebuggerProcess._terminate_process


class ControlOnlyRemote(_FakeProc):
    """Control .echo works while contextual commands wait for a stopped target."""

    def __init__(self, context_prefix=""):
        super().__init__(resumes_on_go=True)
        self.running = True
        self.context_prefix = context_prefix

    def _feed(self, text):
        for line in text.splitlines():
            if line.startswith(".echo "):
                self._handle(line)
            elif self.running:
                self._queued.append(line)
            else:
                self._handle(line)

    def _handle(self, line):
        if line == "r":
            self._out.put(self.context_prefix + "rax=0000000000000000 rbx=0000000000000000")
        elif line == "!peb":
            self._out.put("PEB at 0000000012345678")
        else:
            super()._handle(line)


@pytest.fixture(autouse=True)
def no_native_process_kill(monkeypatch):
    monkeypatch.setattr(
        debug_process.DebuggerProcess, "_terminate_process",
        lambda self: self.process.terminate(),
    )


@pytest.fixture(params=["", "0:000> "])
def remote(monkeypatch, request):
    proc = ControlOnlyRemote(context_prefix=request.param)
    monkeypatch.setattr(cdb_session, "find_executable", lambda *args: "fake")
    monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *args, **kwargs: proc)
    session = CDBSession(remote_connection="tcp:port=5005,server=localhost", timeout=1)
    try:
        yield session, proc
    finally:
        # Fake shutdown must be allowed to quit even when the target is running.
        proc.running = False
        session.shutdown()
        session.reader_thread.join(timeout=1)
        assert not session.reader_thread.is_alive()


class PromptPrefixedMarkerProc(_FakeProc):
    def _handle(self, line):
        if line.startswith(".echo "):
            self._out.put("0:000> " + line[len(".echo "):])
        else:
            super()._handle(line)


def test_native_prompt_prefixed_completion_is_accepted(monkeypatch):
    proc = PromptPrefixedMarkerProc()
    monkeypatch.setattr(debug_session.subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(debug_process.DebuggerProcess, "_terminate_process", lambda self: proc.terminate())
    session = None
    try:
        session = debug_session.DebuggerSession(
            debugger_path="fake", launch_args=["fake"], timeout=0.1, verbose=False,
        )
        assert session.send_command("r", timeout=0.1) == ["OUT:r"]
    finally:
        if session is not None:
            session.shutdown()
        proc.terminate()


def test_resume_does_not_confuse_control_echo_with_a_stopped_target(remote):
    session, proc = remote
    assert not proc.running
    session.send_command("g")
    assert proc.running
    assert session._target_running, "Control echo incorrectly cleared the running flag"


def test_timed_out_output_does_not_leak_into_the_next_command(make_session):
    session, proc = make_session(timeout=1)
    proc._swallow = True
    with pytest.raises(debug_session.DebuggerError, match="timed out"):
        session.send_command("old_command", timeout=0.02)
    assert session.process is None and not proc._alive
    with pytest.raises(debug_session.DebuggerError, match="not running|closed"):
        session.send_command("new_command", timeout=0.2)


def test_close_retry_can_apply_an_updated_resume_policy(make_session, monkeypatch):
    session, proc = make_session(timeout=1)
    policies = []
    session.resume_on_close = False
    monkeypatch.setattr(session, "_release_target", lambda: policies.append(session.resume_on_close))

    def denied_kill():
        raise OSError("access denied")

    def wait(timeout=None):
        if proc._alive:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    monkeypatch.setattr(proc, "wait", wait)
    monkeypatch.setattr(session, "_terminate_process", denied_kill)
    with pytest.raises(debug_session.DebuggerError, match="access denied"):
        session.shutdown()
    session.resume_on_close = True
    monkeypatch.setattr(session, "_terminate_process", proc.terminate)
    session.shutdown()
    assert policies == [False, True] and session.process is None


def test_a_blocked_release_write_cannot_prevent_process_termination(make_session, monkeypatch):
    session, proc = make_session(timeout=1)
    released = threading.Event()
    monkeypatch.setattr(debug_process, "_SHUTDOWN_GRACE_SECONDS", 0.02)
    original_terminate = proc.terminate

    def blocked_release():
        assert released.wait(2), "Release write was never unblocked by termination"

    def terminate():
        original_terminate()
        released.set()

    monkeypatch.setattr(session, "_release_target", blocked_release)
    monkeypatch.setattr(proc, "terminate", terminate)
    started = time.monotonic()
    try:
        session.shutdown()
        assert time.monotonic() - started < 0.5
        assert released.is_set() and session.process is None
        assert not session._release_thread.is_alive()
    finally:
        released.set()


def test_reader_buffer_is_bounded_not_just_the_timeout_message(make_session):
    session, proc = make_session(timeout=1)
    original = proc._handle

    def handle(line):
        if line == "large_output":
            for index in range(5000):
                proc._out.put(f"{index}:" + "x" * 512)
        else:
            original(line)

    proc._handle = handle
    proc._swallow = True
    with pytest.raises(debug_session.DebuggerError, match="timed out") as failure:
        session.send_command("large_output", timeout=0.1)
    assert len("\n".join(failure.value.partial_output)) <= 70 * 1024
    retained = session._reader_buffer or []
    assert len(retained) <= debug_process.MAX_PARTIAL_OUTPUT_LINES, (
        f"The error is bounded, but the reader retains {len(retained)} lines / "
        f"{sum(len(line) for line in retained)} chars"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows tree-kill path")
def test_failed_tree_kill_does_not_forget_a_still_running_debugger(make_session, monkeypatch):
    session, proc = make_session(timeout=1)
    monkeypatch.setattr(server_module, "_sessions", {})
    monkeypatch.setattr(debug_process.DebuggerProcess, "_terminate_process", REAL_TERMINATE)
    kill_commands = []

    def rejected_kill(args, **kwargs):
        kill_commands.append(args)
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"Access is denied")

    def failed_release():
        raise OSError("stdin no longer accepts commands")

    def wait_for_live_process(timeout=None):
        if proc._alive:
            raise subprocess.TimeoutExpired("fake", timeout)
        return proc.returncode

    monkeypatch.setattr(debug_process.subprocess, "run", rejected_kill)
    monkeypatch.setattr(session, "_release_target", failed_release)
    monkeypatch.setattr(proc, "wait", wait_for_live_process)
    cleanup = ExitStack()
    session_id = server_module._register_session(session, "cdb", "fake", cleanup)
    try:
        with pytest.raises(debug_session.DebuggerError, match="taskkill failed"):
            server_module._close_session(session_id, "cdb")
        assert kill_commands
        assert proc._alive and session.process is proc
        assert session_id in server_module._sessions
    finally:
        proc.terminate()
        session.reader_thread.join(timeout=1)
        cleanup.close()


@pytest.mark.anyio
async def test_close_is_not_starved_by_wait_for_break_workers(open_handler, monkeypatch):
    make_handler, session_type, created = open_handler
    release = threading.Event()
    lock = threading.Lock()
    started = 0
    original_shutdown = session_type.shutdown

    def wait_for_break(self, timeout):
        nonlocal started
        with lock:
            started += 1
        if not release.wait(2):
            raise RuntimeError("test failed to release waiting workers")
        return []

    def shutdown(self):
        original_shutdown(self)
        release.set()

    monkeypatch.setattr(session_type, "is_live_session", True, raising=False)
    monkeypatch.setattr(session_type, "wait_for_break", wait_for_break, raising=False)
    monkeypatch.setattr(session_type, "shutdown", shutdown)
    handler = make_handler()
    for name in ("cdb-a", "cdb-b"):
        server_module._sessions[name] = {"session": session_type(), "kind": "cdb", "label": name}
    limiter = anyio.to_thread.current_default_thread_limiter()
    old_tokens = limiter.total_tokens
    limiter.total_tokens = 2  # Same failure at the normal capacity of 40, without 40 threads.
    close_finished = False

    async def wait(name):
        await handler(None, CallToolRequestParams(name="wait_for_break", arguments={"session_id": name}))

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(wait, "cdb-a")
            tasks.start_soon(wait, "cdb-b")
            try:
                with anyio.fail_after(1):
                    while started < 2:
                        await anyio.sleep(0.005)
                with anyio.move_on_after(0.2) as deadline:
                    await handler(None, CallToolRequestParams(name="close_cdb_session", arguments={"session_id": "cdb-a"}))
                close_finished = not deadline.cancel_called
            finally:
                release.set()
    finally:
        limiter.total_tokens = old_tokens
    assert close_finished, (
        "Close waited for the same exhausted limiter as wait_for_break; "
        f"shutdown_calls={[session.shutdown_calls for session in created]}"
    )


def test_unicode_log_success_output_is_also_bounded(make_session):
    session, proc = make_session(timeout=1)
    marker = session._next_marker()
    path = Path(__file__).resolve().parents[3] / "__tmp" / "bounded-unicode-output.ulog"
    path.parent.mkdir(parents=True, exist_ok=True)
    session._log_path = str(path)
    path.write_bytes(
        ("x" * (2 * debug_process.MAX_PARTIAL_OUTPUT_CHARS)
         + f"\n0:000> .echo {marker}\n{marker}\n").encode("utf-16-le")
    )
    session._log_offset = 0
    output = session._read_log_segment(marker)
    assert output is not None
    assert sum(len(line) + 1 for line in output) <= debug_process.MAX_PARTIAL_OUTPUT_CHARS + 1024, (
        f"Successful Unicode-log output is unbounded: {sum(len(line) for line in output)} chars"
    )


def test_verbose_diagnostics_do_not_write_to_mcp_protocol_stdout(make_session, capsys):
    session, proc = make_session(timeout=1)
    session.verbose = True
    session.send_command("r", timeout=0.2)
    captured = capsys.readouterr()
    assert captured.out == "", f"Diagnostic text leaked onto stdio MCP transport: {captured.out!r}"


def test_context_command_after_resume_is_not_reported_complete_before_execution(remote):
    session, proc = remote
    session.send_command("g")
    output = session.send_command("!peb", timeout=0.2)
    assert not proc.running and not proc._queued and output[-1] == "PEB at 0000000012345678", (
        f"Command returned before execution: output={output!r}, "
        f"running={proc.running}, queued={proc._queued!r}, signals={proc.signals!r}"
    )


def test_repeated_remote_probes_do_not_queue_duplicate_register_commands(remote):
    session, proc = remote
    session.send_command("g")
    for _ in range(3):
        with pytest.raises(debug_session.DebuggerContextError):
            session._wait_for_target_context(0.02)
    assert proc._queued.count("r") == 1


def test_successful_single_long_line_is_bounded_and_transport_stays_synced(make_session):
    session, proc = make_session(timeout=1)
    original = proc._handle

    def handle(line):
        if line == "large_line":
            proc._out.put("x" * 200_000)
        else:
            original(line)

    proc._handle = handle
    output = session.send_command("large_line")
    assert "output truncated" in output[-1]
    assert sum(len(line) + 1 for line in output) <= 66 * 1024
    assert session.send_command("r") == ["OUT:r"]


def test_a_marker_inside_a_long_line_fragment_is_not_completion(make_session):
    session, proc = make_session(timeout=1)
    original = proc._handle

    def handle(line):
        if line == "marker_in_payload":
            proc._out.put("x" * debug_process.MAX_PARTIAL_OUTPUT_CHARS + session._expected_marker)
        else:
            original(line)

    proc._handle = handle
    proc._swallow = True
    with pytest.raises(debug_session.DebuggerError, match="synchronization was lost"):
        session.send_command("marker_in_payload", timeout=0.02)


def test_exit_diagnostics_survive_a_full_presentation_buffer(make_session):
    session, proc = make_session(timeout=1)
    original = proc._handle

    def handle(line):
        if line == "crash_after_output":
            proc.exit_with("x" * 200_000, "fatal native error", code=0xC0000005)
        elif proc._alive:
            original(line)

    proc._handle = handle
    with pytest.raises(debug_session.DebuggerExitedError, match="fatal native error") as failure:
        session.send_command("crash_after_output")
    assert len(str(failure.value)) < 30 * 1024


def test_completed_commands_rotate_and_remove_unicode_logs(make_session, monkeypatch):
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: True)
    monkeypatch.setattr(debug_process, "ROTATE_LOG_BYTES", 4096)
    session, proc = make_session(timeout=1)
    old_path = Path(session._log_path)
    original = proc._handle
    payload = "x" * 8000

    def handle(line):
        if line == "large_logged_output":
            proc._out.put(payload)
            proc._log_write(payload + "\r\n")
        else:
            original(line)

    proc._handle = handle
    assert session.send_command("large_logged_output") == [payload]
    assert not old_path.exists()
    assert session._log_path != str(old_path) and session._log_active
    assert session.send_command("r") == ["OUT:r"]


def test_a_hung_oversized_unicode_log_closes_and_removes_owned_resources(make_session, monkeypatch):
    monkeypatch.setattr(debug_session, "_acp_is_multibyte", lambda: True)
    session, proc = make_session(timeout=1)
    path = Path(session._log_path)
    proc._log_path = None  # Do not let the fake recreate the removed file on quit.
    with path.open("ab") as handle:
        handle.write(b"x\x00" * (debug_process.MAX_LOG_BYTES // 2))
    session._last_log_check = 0
    session._check_log_size()
    with pytest.raises(debug_session.DebuggerError, match="8 MiB safety threshold"):
        session._raise_if_exited("while testing the safety limit")
    assert not proc._alive and session.process is None
    assert not path.exists() and not session.reader_thread.is_alive()


def test_error_file_logging_is_asynchronous_and_bounded(monkeypatch):
    from mcp_windbg import error_log
    emitted = threading.Event()
    observations = {}
    caller = threading.get_ident()

    class Sink(logging.Handler):
        def __init__(self, path, **options):
            super().__init__()
            observations.update(path=path, options=options)

        def emit(self, record):
            observations.update(thread=threading.get_ident(), message=record.getMessage())
            emitted.set()

    monkeypatch.setattr(error_log, "_logger", logging.Logger("isolated-test"))
    monkeypatch.setattr(error_log, "_listener", None)
    monkeypatch.setattr(error_log.logging.handlers, "RotatingFileHandler", Sink)
    try:
        error_log.log_error("test diagnostic %s", "message")
        assert emitted.wait(1)
        assert observations["thread"] != caller
        assert observations["message"] == "test diagnostic message"
        assert observations["path"].name == "errors.log"
        assert observations["options"] == {"maxBytes": 1024 * 1024, "backupCount": 3, "encoding": "utf-8"}
        assert error_log._listener.queue.maxsize == 1024
    finally:
        error_log._stop_listener()
