"""Shared machinery for driving a CDB/KD debugger subprocess.

Both the user-mode debugger (``cdb.exe``, see :mod:`cdb_session`) and the kernel
debugger (``kd.exe``, see :mod:`kd_session`) talk to their process the same way:
launch it on a pipe, read its stdout on a background thread, and detect when a
command has finished by echoing a unique marker after it. That protocol lives
here once; the two session types only differ in how they are launched and how
they reach their first prompt.

Two robustness properties this base guarantees:

- **Per-command markers.** Every command echoes ``COMMAND_COMPLETED_MARKER_<n>``
  with a monotonic ``<n>``. The reader only completes on the marker the current
  command is waiting for, so a slow command whose output arrives late can never
  be mistaken for the next command's completion.
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
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from typing import List, Optional

# Detects a CDB/KD prompt line such as ``0:000>`` or ``3: kd>``.
PROMPT_REGEX = re.compile(r"^\d+:.*>\s*$")

# Base text of the per-command completion marker; a monotonic sequence number is
# appended so each command waits for its own, distinct marker.
MARKER_BASE = "COMMAND_COMPLETED_MARKER"

# How long ``wait_for_break`` blocks by default. Waiting on a resumed target is
# open-ended by nature - you are waiting for a bugcheck or a breakpoint - so this
# is deliberately unrelated to the per-command timeout.
DEFAULT_WAIT_FOR_BREAK_TIMEOUT = 300

# How long _break_in_and_resync waits for a target it believes is running to
# answer on its own before resorting to CTRL+BREAK. Only ever paid after a
# go-class command, and only when the target really is still going.
BREAK_IN_PROBE_TIMEOUT = 2


class DebuggerError(Exception):
    """Raised for any debugger session failure (launch, timeout, I/O)."""


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


class DebuggerSession:
    """A debugger subprocess plus the marker protocol used to drive it.

    Subclasses set ``is_live_session`` and provide their launch arguments and a
    ``_startup`` that reaches the first prompt. Everything else - the reader
    thread, ``send_command``, timeout handling, and shutdown - is shared.
    """

    #: Whether this attaches to a running target (remote/kernel) rather than a
    #: static dump. Live sessions get their own process group (so CTRL+BREAK can
    #: break in) and are detached with CTRL+B instead of quit with ``q``.
    is_live_session: bool = False

    def __init__(
        self,
        *,
        debugger_path: str,
        launch_args: List[str],
        timeout: int,
        verbose: bool,
    ):
        self.debugger_path = debugger_path
        self.timeout = timeout
        self.verbose = verbose

        self.output_lines: List[str] = []
        self.lock = threading.Lock()
        #: Serializes whole operations on the debugger's stdin. ``self.lock``
        #: only guards individual field writes; it cannot make "install a
        #: marker, wait for it, take the output" atomic, and since
        #: ``wait_for_break`` parks for minutes on a worker thread there is
        #: real overlap to guard against. ``send_ctrl_break`` deliberately does
        #: not take it - it is the escape hatch from a long wait.
        self._io_lock = threading.RLock()
        self.ready_event = threading.Event()
        self._marker_seq = 0
        self._expected_marker: Optional[str] = None
        #: True between a go-class command and the next break-in. While set, the
        #: debugger is not reading its input, so the marker protocol is unusable.
        self._target_running = False
        #: Set by shutdown so a parked wait stops rather than outliving the session.
        self._closing = False

        try:
            creationflags = 0
            if os.name == "nt" and self.is_live_session:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            self.process: Optional[subprocess.Popen] = subprocess.Popen(
                launch_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as e:  # pragma: no cover - Popen rarely fails once the exe is located
            raise DebuggerError(f"Failed to start debugger process: {e}")

        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

        self._startup()

    # -- Subclass hooks ---------------------------------------------------

    def _startup(self) -> None:
        """Reach the first usable prompt. Overridden by kernel sessions."""
        try:
            self._wait_for_prompt(self.timeout)
        except DebuggerError:
            self.shutdown()
            raise DebuggerError("Debugger initialization timed out")

    def _on_output_line(self, line: str) -> None:
        """Called (under ``self.lock``) for every output line. Kernel uses it
        to notice the ``Connected to target`` banner."""

    # -- Reader thread ----------------------------------------------------

    def _read_output(self) -> None:
        if not self.process or not self.process.stdout:
            return

        buffer: List[str] = []
        try:
            for line in self.process.stdout:
                line = line.rstrip()
                if self.verbose:
                    print(f"DBG > {line}")

                with self.lock:
                    if MARKER_BASE in line:
                        # A marker line is ours, never the target's. Either it is
                        # the one being waited on, or it is an earlier marker we
                        # abandoned on a timeout - which must not be published as
                        # if the debugger had printed it.
                        self._on_output_line(line)
                        if self._expected_marker and self._expected_marker in line:
                            self.output_lines = buffer
                            buffer = []
                            self._expected_marker = None
                            self.ready_event.set()
                        continue
                    buffer.append(line)
                    self._on_output_line(line)
        except (IOError, ValueError, AttributeError) as e:
            if self.verbose:
                print(f"Debugger output reader error: {e}")

    # -- Command protocol -------------------------------------------------

    def _next_marker(self) -> str:
        self._marker_seq += 1
        return f"{MARKER_BASE}_{self._marker_seq}"

    def _take_output(self) -> List[str]:
        """Detach and return whatever the reader has published."""
        with self.lock:
            result = self.output_lines.copy()
            self.output_lines = []
        return result

    def _abandon_marker(self) -> bool:
        """Give up on the marker currently being waited for.

        Returns True if the marker in fact landed in the moment between the
        deadline expiring and this call - in which case nothing was discarded
        and the caller should treat its operation as having succeeded. The check
        runs under ``self.lock``, which the reader also holds while it publishes,
        so there is no window where a bugcheck banner can be thrown away for
        having arrived a microsecond late.

        Otherwise the ``.echo`` stays queued in the debugger and will print
        whenever it finally gets read; the reader drops stray markers, so it goes
        nowhere. What matters is that no later wait inherits this one's
        half-finished state.
        """
        with self.lock:
            if self._expected_marker is None and self.ready_event.is_set():
                return True
            self._expected_marker = None
            self.output_lines = []
        self.ready_event.clear()
        return False

    def _exclusive(self):
        """Acquire the session's I/O lock, or explain why the session is busy.

        Never waits. The server calls most tools straight from its event loop,
        so blocking here would stall every other session too - and would stall
        the very ``send_ctrl_break`` the failure message points at.
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
        try:
            self.process.stdin.write(f".echo {marker}\n")
            self.process.stdin.flush()
        except (IOError, ValueError, AttributeError) as e:
            raise DebuggerError(f"Failed to communicate with debugger: {e}")

        if not self.ready_event.wait(timeout or self.timeout):
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

    def _send_marked(
        self, command: str, cmd_timeout: int, preamble: Optional[List[str]] = None
    ) -> List[str]:
        """Write *command* with a completion marker and return its output."""
        marker = self._next_marker()
        self.ready_event.clear()
        with self.lock:
            self.output_lines = []
            self._expected_marker = marker

        try:
            self.process.stdin.write(f"{command}\n.echo {marker}\n")
            self.process.stdin.flush()
        except (IOError, ValueError, AttributeError) as e:
            raise DebuggerError(f"Failed to send command: {e}")

        landed = self.ready_event.wait(cmd_timeout)
        if self._closing:
            raise DebuggerError("Session was closed while the command was running")
        if not landed and not self._marker_landed():
            resynced = self._abort_running_command()
            detail = "" if resynced else " (session may need a manual break-in)"
            # The break-in output is the only record of why the target stopped
            # and would otherwise die with this exception, so it rides along.
            lost = (
                "\nThe target had stopped with:\n" + "\n".join(preamble)
                if preamble
                else ""
            )
            raise DebuggerError(
                f"Command timed out after {cmd_timeout} seconds: {command}{detail}{lost}"
            )

        return self._take_output()

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
        return pending + [
            f"Target resumed with '{command}'; it is running and produces no "
            "output until it stops.",
            "Wait for it with wait_for_break, or halt it with send_ctrl_break. Any "
            "other command breaks in automatically first.",
        ]

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

        try:
            self.process.stdin.write(f".echo {marker}\n")
            self.process.stdin.flush()
        except (IOError, ValueError, AttributeError) as e:
            raise DebuggerError(f"Failed to communicate with debugger: {e}")

        wait = timeout or DEFAULT_WAIT_FOR_BREAK_TIMEOUT
        landed = self.ready_event.wait(wait)
        if self._closing:
            raise DebuggerError("Session was closed while waiting for the target to stop")
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

    def _release_target(self) -> None:
        """Tell the debugger to release the target before the process is dropped.

        - A dump session quits with ``q``.
        - A live user-mode remote detaches with CTRL+B, which resumes the target.

        Kernel sessions override this: CTRL+B does not resume a kernel target
        (only ``g`` does), so :class:`~mcp_windbg.kd_session.KDSession` handles it.
        """
        if self.is_live_session:
            self.process.stdin.write("\x02")  # CTRL+B detaches a user-mode remote
        else:
            self.process.stdin.write("q\n")
        self.process.stdin.flush()

    def shutdown(self) -> None:
        """Release the target, then terminate the debugger process.

        Deliberately does not take ``_io_lock``: closing a session has to work
        while a ``wait_for_break`` is parked on it, which is precisely when the
        lock is held. Instead it flags the session closed and wakes the waiter,
        which then reports the close rather than sitting out its full timeout on
        a debugger that no longer exists.
        """
        self._closing = True
        self.ready_event.set()
        try:
            if self.process and self.process.poll() is None:
                try:
                    self._release_target()
                    self.process.wait(timeout=2)
                except Exception:
                    pass

                if self.process.poll() is None:
                    self._terminate_process()
        except Exception as e:
            if self.verbose:
                print(f"Error during shutdown: {e}")
        finally:
            self.process = None

    def _terminate_process(self) -> None:
        """Kill the debugger process. On Windows use a tree kill: cdb.exe/kd.exe
        launched via the Microsoft Store execution aliases spawn a child that a
        plain terminate() leaves behind holding the target/connection."""
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                capture_output=True,
            )
        else:  # pragma: no cover - project is Windows-only
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except Exception:
            pass

    def __enter__(self):  # pragma: no cover - convenience API, not used by the server
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # pragma: no cover
        self.shutdown()
