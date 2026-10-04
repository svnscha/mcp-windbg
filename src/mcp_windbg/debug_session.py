"""Shared machinery for driving a CDB/KD debugger subprocess.

Both the user-mode debugger (``cdb.exe``, see :mod:`cdb_session`) and the kernel
debugger (``kd.exe``, see :mod:`kd_session`) talk to their process the same way:
launch it on a pipe, read its stdout on a background thread, and detect when a
command has finished by echoing a unique marker after it. That protocol lives
here once; the two session types only differ in how they are launched and how
they reach their first prompt.

Robustness properties of this shared implementation:

- **Session-unique per-command markers.** Every command echoes
  ``COMMAND_COMPLETED_MARKER_<nonce>_<n>`` with a random session nonce and a
  monotonic counter. Only the exact standalone output completes the command;
  delayed markers, another client's markers and echoed commands cannot do so.
- **Cancel-on-timeout for live targets.** When a command on a live session
  (user-mode remote or kernel) outruns its timeout, the debugger is still busy
  executing it. We send CTRL+BREAK to break back in, drain to the pending
  marker, and only then report the timeout - leaving the session resynchronized
  instead of wedged.
- **Go-class commands do not use the marker at all.** ``g`` and its relatives
  hand the CPU back to the target, and the debugger stops reading stdin until
  the target stops again. Marking them would queue an ``.echo`` the debugger
  cannot answer, so the command would "time out" and the resulting CTRL+BREAK
  would halt the target we just released. They are written bare instead, and
  ``wait_for_break`` picks up whatever the target prints when it does stop.
- **Exit is noticed, not timed out.** When the debugger process exits (``q``, a
  failed ``-remote``/``-k`` connect, cdb itself crashing), the reader sees EOF
  and wakes whoever is waiting, who reports the exit status and the debugger's
  last output instead of sitting out the timeout and blaming the command.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import time
from typing import List, Optional

from .debug_process import (
    DebuggerError, DebuggerExitedError, DebuggerProcess, MARKER_BASE,
    MAX_PARTIAL_OUTPUT_LINES, MAX_PARTIAL_OUTPUT_CHARS,
    _acp_is_multibyte, _extract_log_output,
)

PROMPT_REGEX = re.compile(r"^\d+:.*>\s*$")


DEFAULT_WAIT_FOR_BREAK_TIMEOUT = 300


BREAK_IN_PROBE_TIMEOUT = 2


RESUME_CONFIRM_TIMEOUT = 0.5


class _ReleaseOnExit:
    """Context manager that releases an already-acquired lock on exit."""

    def __init__(self, lock):
        self._lock = lock

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._lock.release()
        return False


def build_debugger_args(
    debugger_path: str,
    dump_path: Optional[str] = None,
    remote_connection: Optional[str] = None,
    kernel_connection: Optional[str] = None,
    symbols_path: Optional[str] = None,
    additional_args: Optional[List[str]] = None,
) -> List[str]:
    """Assemble the debugger command line for a session.

    Exactly one of ``dump_path``, ``remote_connection``, or ``kernel_connection``
    selects how the debugger attaches:

    - ``dump_path`` opens a crash dump with ``-z``.
    - ``remote_connection`` attaches a user-mode debugger *client* to an existing
      debug *server* with ``-remote`` (e.g. ``tcp:Port=5005,Server=host``).
    - ``kernel_connection`` attaches to a kernel target with ``-k`` (KDNET
      ``net:port=,key=``, named pipe ``com:pipe,port=\\\\.\\pipe\\name,...``, or
      serial ``com:port=COM1,baud=115200``).

    ``-remote`` and ``-k`` are different mechanisms: ``-remote`` cannot drive a
    kernel cable and ``-k`` cannot drive a user-mode debug server.
    """
    args = [debugger_path]

    if dump_path:
        args.extend(["-z", dump_path])
    elif remote_connection:
        args.extend(["-remote", remote_connection])
    elif kernel_connection:
        args.extend(["-k", kernel_connection])

    if symbols_path:
        args.extend(["-y", symbols_path])

    if additional_args:
        args.extend(additional_args)

    return args


def find_executable(paths: List[str], custom_path: Optional[str] = None) -> Optional[str]:
    """Return the first existing path (custom first, then the defaults)."""
    if custom_path and os.path.isfile(custom_path):
        return custom_path
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


class DebuggerSession(DebuggerProcess):
    """Command sequencing over a shared debugger subprocess and output reader."""

    def _startup(self) -> None:
        """Reach the first usable prompt. Overridden by kernel sessions."""
        try:
            self._wait_for_prompt(self.timeout)
        except DebuggerExitedError:
            self.shutdown()
            raise
        except DebuggerError:
            self.shutdown()
            raise DebuggerError("Debugger initialization timed out")

    def _next_marker(self) -> str:
        self._marker_seq += 1
        return f"{MARKER_BASE}_{self._marker_nonce}_{self._marker_seq}"

    def _exclusive(self):
        """Acquire the session's I/O lock, or explain why the session is busy.

        Never waits: queued operations on a busy session would occupy debugger
        workers needed by other sessions. ``send_ctrl_break`` remains available
        without acquiring this lock.
        """
        if not self._io_lock.acquire(blocking=False):
            raise DebuggerError(
                "This session is busy with another operation - most likely a "
                "wait_for_break parked on a running target. Use send_ctrl_break "
                "to stop the target, which ends the wait."
            )
        return _ReleaseOnExit(self._io_lock)

    def _wait_for_prompt(self, timeout: Optional[int] = None) -> None:
        """Send a bare marker and wait for it, proving the prompt is ready."""
        marker = self._next_marker()
        self.ready_event.clear()
        with self.lock:
            self._expected_marker = marker
        doing = "before reaching a prompt"
        self._raise_if_exited(doing)
        self._write_input(f".echo {marker}\n", doing, "Failed to communicate with debugger")

        landed = self.ready_event.wait(timeout or self.timeout)
        self._raise_if_exited(doing)
        if not landed:
            raise DebuggerError("Timed out waiting for debugger prompt")

    #: Commands that hand the target back its CPU and do not return to a prompt
    #: on their own. The debugger stops reading stdin until the target stops
    #: again, so the marker protocol cannot be used for these: the ``.echo``
    #: would sit unread in the input queue, ``send_command`` would hit its
    #: timeout, and the CTRL+BREAK that timeout fires would halt the very target
    #: the command just released.
    #:
    #: The step family (``p``, ``t``, ``pa``, ``ta``, ``pt``, ``tt`` ...) is
    #: deliberately absent. Those execute one instruction or source line and come
    #: straight back to the prompt, so the normal marker round-trip is both
    #: correct and necessary - classifying them as go-class would swallow their
    #: output and leave the session wrongly believing the target is running.
    #:
    #: Not modelled: a go buried in a brace body, as in
    #: ``.if (@eax==0) { g }``. Those still take the marker path and hit the
    #: original timeout-then-CTRL+BREAK behaviour. Scripted control flow is rare
    #: from a tool caller, and parsing it properly means parsing the debugger's
    #: whole expression syntax; write the ``g`` as its own command instead.
    _GO_COMMANDS = frozenset({"g", "gh", "gn", "gN", "gc", "gu"})

    #: Leading thread/process qualifier, as in ``~0 g``, ``~*g``, ``|1s``.
    _THREAD_QUALIFIER = re.compile(r"^[~|][0-9*.#]*\s*")

    @staticmethod
    def _split_segments(command: str) -> List[str]:
        """Split a command line on ``;``, respecting double-quoted strings.

        ``bp nt!NtCreateFile ".echo hit; g"`` is one command that sets a
        breakpoint, not two of which the second resumes the target - the ``; g``
        lives inside the breakpoint's own command string and runs later, if ever.
        """
        segments, current, in_quotes = [], [], False
        for char in command:
            if char == '"':
                in_quotes = not in_quotes
                current.append(char)
            elif char == ";" and not in_quotes:
                segments.append("".join(current))
                current = []
            else:
                current.append(char)
        segments.append("".join(current))
        return segments

    @classmethod
    def _go_segment_index(cls, command: str) -> Optional[int]:
        """Index of the first segment that hands the CPU back, or None.

        Recognizes the qualified forms a caller actually writes, not just a bare
        ``g``: ``g 0x7ffb1234``, ``~0 g``, ``~*g``.
        """
        for index, segment in enumerate(cls._split_segments(command)):
            segment = cls._THREAD_QUALIFIER.sub("", segment.strip())
            head = segment.split()[:1]
            if head and head[0] in cls._GO_COMMANDS:
                return index
        return None

    @classmethod
    def _is_go_command(cls, command: str) -> bool:
        """True if *command* hands the CPU back to the target."""
        return cls._go_segment_index(command) is not None

    def send_command(self, command: str, timeout: Optional[int] = None) -> List[str]:
        """Send a command and return its output lines.

        On a live session, a command that outruns ``timeout`` is aborted with
        CTRL+BREAK and the session is resynchronized before the timeout is
        reported, so the next command starts from a clean prompt.

        Go-class commands on a live session are fire-and-forget: they are written
        without a marker and return immediately, leaving the target running. Use
        :meth:`wait_for_break` to block until it stops, or :meth:`send_ctrl_break`
        to halt it. Issuing an ordinary command while the target runs breaks in
        first, so callers never have to sequence that themselves.

        Raises:
            DebuggerError: if the process is gone, I/O fails, or the command
                times out.
        """
        if not self.process:
            raise DebuggerError("Debugger process is not running")
        if self._debugger_exited:
            raise DebuggerExitedError(
                "Debugger process has exited; this session can no longer be used. "
                "Close it and open a new one."
            )

        cmd_timeout = timeout or self.timeout

        with self._exclusive():
            if self.is_live_session:
                go_at = self._go_segment_index(command)
                if go_at is not None:
                    return self._run_then_resume(command, go_at, cmd_timeout)

            # Anything the target printed on the way to stopping - a bugcheck
            # banner, a breakpoint report - is why it stopped, so it leads the
            # output rather than being dropped on the floor.
            preamble = self._break_in_and_resync() if self._target_running else []
            return preamble + self._send_marked(command, cmd_timeout, preamble)

    def _enable_unicode_log(self) -> None:
        """On a multibyte code page, mirror output to a UTF-16 log and read
        command output from it instead of the truncating ANSI pipe.

        cdb/kd write pipe (and ANSI-log) output short by each line's multibyte
        expansion, losing the tail of any non-ASCII line; ``.logopen /u`` writes
        a UTF-16 log that is complete and flushes per command. The pipe stays the
        sync channel - the ``.echo`` markers are logged too, so each command's
        output is the log slice ahead of its marker. Best effort: any failure
        leaves the session on the pipe path exactly as before.
        """
        if not _acp_is_multibyte() or not self._engine_is_local:
            return
        try:
            fd, path = tempfile.mkstemp(prefix="mcp_windbg_", suffix=".ulog")
            os.close(fd)
            os.remove(path)  # cdb creates it; a pre-existing file would be appended to
            self._send_marked(f".logopen /u {path}", self.timeout)
            if not os.path.exists(path):
                return
            self._log_path = path
            self._log_offset = 0
            self._log_active = True
            # The log opens mid-command, so it starts with its banner and the
            # logopen echo. One throwaway marked command drains all of that and
            # leaves the offset at a clean boundary - robust to log-flush timing
            # in a way that trusting the post-open file size is not.
            self._send_marked(".echo", self.timeout)
        except Exception:
            self._cleanup_log()

    def _marker_landed(self) -> bool:
        """True if the pending marker arrived just as the deadline expired."""
        with self.lock:
            return self._expected_marker is None and self.ready_event.is_set()

    def _run_then_resume(self, command: str, go_at: int, cmd_timeout: int) -> List[str]:
        """Run everything before the go segment, then resume with the rest.

        ``bp nt!NtCreateFile; g`` is one line to the caller but two things to the
        session. Sending it whole would throw away ``Breakpoint 0 set`` - or
        ``Couldn't resolve error at 'nt!NtCreateFle'``, which is the difference
        between waiting 300 seconds for a break and knowing it can never come.
        """
        segments = self._split_segments(command)
        prefix = ";".join(segments[:go_at]).strip()
        rest = ";".join(segments[go_at:]).strip()

        output: List[str] = []
        if prefix:
            output.extend(self._break_in_and_resync() if self._target_running else [])
            output.extend(self._send_marked(prefix, cmd_timeout, output))
        output.extend(self._resume_target(rest))
        return output

    def _abort_running_command(self) -> bool:
        """Break into a live target still running a timed-out command.

        Sends CTRL+BREAK, then waits briefly for the pending marker to arrive so
        the session lands back at a clean prompt. Returns True if it resynced.
        For a dump (not live) there is nothing to break into.
        """
        resynced = False
        if self.is_live_session and self.process and self.process.poll() is None:
            try:
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
                # The queued marker runs once the target breaks in; wait for it.
                resynced = self.ready_event.wait(min(10, max(3, self.timeout)))
            except Exception:
                resynced = False
        with self.lock:
            self.output_lines = []
            self._expected_marker = None
        return resynced

    def _resume_target(self, command: str) -> List[str]:
        """Write a go-class command and return without waiting for a prompt.

        Refuses to write a second one while the target is already running: the
        debugger is not reading stdin, so it would sit queued and resume the
        target again the moment it stopped - after which the session's idea of
        what the target is doing would be wrong in the one direction that costs
        a full command timeout to discover.
        """
        pending: List[str] = []
        if self._target_running:
            if self._still_running():
                return [
                    f"Target is already running; '{command}' was not sent. "
                    f"Wait for it with wait_for_break, or halt it with send_ctrl_break."
                ]
            # It had stopped after all - an earlier send_ctrl_break landing, or a
            # breakpoint. What it printed on the way is why, and leads the reply
            # rather than being wiped by the next operation.
            self._target_running = False
            pending = self._take_output()
        if self._abandon_marker():
            pending.extend(self._take_output())
        try:
            self.process.stdin.write(f"{command}\n")
            self.process.stdin.flush()
        except (IOError, ValueError, AttributeError) as e:
            raise DebuggerError(f"Failed to send command: {e}")
        self._target_running = True

        if not self._resume_took_effect():
            # The debugger answered, so the target is already back at a prompt:
            # a `gu` that returned in microseconds, or a breakpoint hit at once.
            # Its output is the useful answer, not a claim that it is running.
            self._target_running = False
            return pending + self._take_output()

        return pending + [
            f"Target resumed with '{command}'; it is running and produces no "
            "output until it stops.",
            "Wait for it with wait_for_break, or halt it with send_ctrl_break. Any "
            "other command breaks in automatically first.",
        ]

    def _resume_took_effect(self) -> bool:
        """True once the debugger has consumed the resume and the target is away.

        Probes with a marker the debugger can only answer from a prompt. Silence
        means it has stopped reading stdin, which is exactly what running the
        target looks like from here - and, crucially, means the resume is no
        longer sitting unread in the input queue where it would swallow the next
        CTRL+BREAK. An answer means the target never left, or came straight back.
        """
        try:
            self._wait_for_prompt(RESUME_CONFIRM_TIMEOUT)
        except DebuggerExitedError:
            raise
        except DebuggerError:
            return not self._abandon_marker()
        return False

    def _break_in_and_resync(self) -> List[str]:
        """Get a running target back to a prompt, and return what it printed.

        Called automatically by :meth:`send_command` when an ordinary command is
        issued while a go-class command still has the target running.

        Asks before it signals, via :meth:`_still_running`. A CTRL+BREAK aimed at
        a target that is in fact halted is not free - a kernel target queues the
        break request and stops itself again later, after we thought we had
        released it.

        Raises:
            DebuggerError: if the break-in signal fails or no prompt follows it.
        """
        if not self._still_running():
            self._target_running = False
            return self._take_output()

        try:
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception as e:
            raise DebuggerError(f"Failed to break into the running target: {e}")
        try:
            self._wait_for_prompt(min(10, max(3, self.timeout)))
        except DebuggerExitedError:
            raise
        except DebuggerError:
            if not self._abandon_marker():
                raise DebuggerError(
                    "Target did not stop after CTRL+BREAK and is still running; "
                    "it may be wedged below the debugger's reach."
                ) from None
        self._target_running = False
        return self._take_output()

    def _still_running(self) -> bool:
        """Probe the debugger for a prompt; False means the target is stopped.

        ``_target_running`` records that we resumed the target, not that it is
        *still* going: a ``gu`` returns in microseconds, a ``g`` can hit a
        breakpoint at once, and an earlier ``send_ctrl_break`` may already have
        landed. Only the debugger knows, so ask it. On a False the output it
        printed on the way to stopping is left published for the caller to take.
        """
        try:
            self._wait_for_prompt(BREAK_IN_PROBE_TIMEOUT)
        except DebuggerExitedError:
            raise
        except DebuggerError:
            return not self._abandon_marker()
        return False

    def wait_for_break(self, timeout: Optional[int] = None) -> List[str]:
        """Block until a resumed target stops, and return what it printed.

        Queues a marker into the debugger's stdin. While the target runs the
        debugger is not reading input, so the marker sits there; when the target
        stops - a bugcheck, a breakpoint, a manual break-in - the debugger drains
        its input and echoes it. Everything printed in between (the crash banner,
        the breakpoint report) arrives ahead of the marker and is returned.

        The marker is queued unconditionally rather than short-circuiting on our
        own "is it running" flag: the debugger answering at once *is* the proof
        that the target is stopped, and it is right about targets this session
        never resumed itself.

        Args:
            timeout: Seconds to wait. Not bounded by the session timeout: waiting
                on a target is expected to be long.

        Raises:
            DebuggerError: if the process is gone, I/O fails, or the target is
                still running when *timeout* expires.
        """
        if not self.process or self.process.poll() is not None:
            raise DebuggerError("Debugger process is not running")

        with self._exclusive():
            return self._wait_for_break_locked(timeout)

    def _wait_for_break_locked(self, timeout: Optional[int]) -> List[str]:
        was_running = self._target_running
        marker = self._next_marker()
        self.ready_event.clear()
        with self.lock:
            self.output_lines = []
            self._expected_marker = marker
        doing = "while waiting for the target to stop"
        self._raise_if_exited(doing)
        self._write_input(f".echo {marker}\n", doing, "Failed to communicate with debugger")

        wait = timeout or DEFAULT_WAIT_FOR_BREAK_TIMEOUT
        landed = self.ready_event.wait(wait)
        if self._closing:
            raise DebuggerError("Session was closed while waiting for the target to stop")
        self._raise_if_exited(doing)
        if not landed and not self._abandon_marker():
            raise DebuggerError(
                f"Target did not stop within {wait} seconds and is still running. "
                f"Wait again, or halt it with send_ctrl_break."
            )

        self._target_running = False
        result = self._take_output()
        if not was_running and not result:
            return ["Target was already stopped; there was nothing to wait for."]
        return result

    def send_ctrl_break(self) -> None:
        """Deliver CTRL+BREAK to break into a running target.

        Raises:
            DebuggerError: if the process is not running or the signal fails.
        """
        if not self.process or self.process.poll() is not None:
            raise DebuggerError("Debugger process is not running")
        try:
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception as e:
            raise DebuggerError(f"Failed to send CTRL+BREAK: {e}")
        # _target_running is deliberately left alone. CTRL+BREAK only *requests*
        # a break; over KDNET or a serial cable it can take seconds to land, and
        # clearing the flag here would have the next command write into a
        # debugger that is still not reading. Leaving it set costs one cheap
        # probe in _break_in_and_resync, which resolves the truth either way.

    # -- Teardown ---------------------------------------------------------
