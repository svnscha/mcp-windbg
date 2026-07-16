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


class DebuggerError(Exception):
    """Raised for any debugger session failure (launch, timeout, I/O)."""


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
        self.ready_event = threading.Event()
        self._marker_seq = 0
        self._expected_marker: Optional[str] = None

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
                    buffer.append(line)
                    self._on_output_line(line)
                    if self._expected_marker and self._expected_marker in line:
                        # Drop the marker line itself before publishing.
                        if buffer and self._expected_marker in buffer[-1]:
                            buffer.pop()
                        self.output_lines = buffer
                        buffer = []
                        self._expected_marker = None
                        self.ready_event.set()
        except (IOError, ValueError) as e:
            if self.verbose:
                print(f"Debugger output reader error: {e}")

    # -- Command protocol -------------------------------------------------

    def _next_marker(self) -> str:
        self._marker_seq += 1
        return f"{MARKER_BASE}_{self._marker_seq}"

    def _wait_for_prompt(self, timeout: Optional[int] = None) -> None:
        """Send a bare marker and wait for it, proving the prompt is ready."""
        marker = self._next_marker()
        self.ready_event.clear()
        with self.lock:
            self._expected_marker = marker
        try:
            self.process.stdin.write(f".echo {marker}\n")
            self.process.stdin.flush()
        except (IOError, ValueError) as e:
            raise DebuggerError(f"Failed to communicate with debugger: {e}")

        if not self.ready_event.wait(timeout or self.timeout):
            raise DebuggerError("Timed out waiting for debugger prompt")

    def send_command(self, command: str, timeout: Optional[int] = None) -> List[str]:
        """Send a command and return its output lines.

        On a live session, a command that outruns ``timeout`` is aborted with
        CTRL+BREAK and the session is resynchronized before the timeout is
        reported, so the next command starts from a clean prompt.

        Raises:
            DebuggerError: if the process is gone, I/O fails, or the command
                times out.
        """
        if not self.process:
            raise DebuggerError("Debugger process is not running")

        marker = self._next_marker()
        self.ready_event.clear()
        with self.lock:
            self.output_lines = []
            self._expected_marker = marker

        try:
            self.process.stdin.write(f"{command}\n.echo {marker}\n")
            self.process.stdin.flush()
        except (IOError, ValueError) as e:
            raise DebuggerError(f"Failed to send command: {e}")

        cmd_timeout = timeout or self.timeout
        if not self.ready_event.wait(cmd_timeout):
            resynced = self._abort_running_command()
            detail = "" if resynced else " (session may need a manual break-in)"
            raise DebuggerError(
                f"Command timed out after {cmd_timeout} seconds: {command}{detail}"
            )

        with self.lock:
            result = self.output_lines.copy()
            self.output_lines = []
        return result

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

    # -- Teardown ---------------------------------------------------------

    def shutdown(self) -> None:
        """Terminate the debugger process, detaching a live target cleanly."""
        try:
            if self.process and self.process.poll() is None:
                try:
                    if self.is_live_session:
                        self.process.stdin.write("\x02")  # CTRL+B detaches
                        self.process.stdin.flush()
                    else:
                        self.process.stdin.write("q\n")
                        self.process.stdin.flush()
                    self.process.wait(timeout=1)
                except Exception:
                    pass

                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=3)
        except Exception as e:
            if self.verbose:
                print(f"Error during shutdown: {e}")
        finally:
            self.process = None

    def __enter__(self):  # pragma: no cover - convenience API, not used by the server
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # pragma: no cover
        self.shutdown()
