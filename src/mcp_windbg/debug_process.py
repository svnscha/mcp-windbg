"""Debugger subprocess, output reader, Unicode log and resource ownership."""
from __future__ import annotations

import locale
import os
from collections import deque
import subprocess
import sys
import threading
import time
import uuid
from typing import List, Optional

from .debug_output import (
    BoundedOutput, MARKER_BASE, MARKER_LINE as _MARKER_LINE,
    LOGGED_PROMPT as _LOGGED_PROMPT, MAX_LOG_BYTES, ROTATE_LOG_BYTES,
    MAX_OUTPUT_LINES as MAX_PARTIAL_OUTPUT_LINES,
    MAX_OUTPUT_CHARS as MAX_PARTIAL_OUTPUT_CHARS, output_lines, read_log_segment,
)
from .error_log import log_error

_EXIT_TAIL_LINES = 20
_SHUTDOWN_GRACE_SECONDS = 2.0


class DebuggerError(Exception):
    """Raised for any debugger session failure (launch, timeout, I/O)."""

    def __init__(self, message: str, *, partial_output: Optional[List[str]] = None):
        super().__init__(message)
        self.partial_output = list(partial_output or [])


class DebuggerExitedError(DebuggerError):
    """The debugger process exited; the session cannot be used again."""


class DebuggerPromptTimeoutError(DebuggerError):
    """The marker did not arrive before a probe deadline."""


class DebuggerContextError(DebuggerError):
    """The remote transport replied but no stopped thread context was proven."""


def _debugger_output_encoding() -> str:
    """The code page cdb/kd write their output in - the process ANSI code page,
    which ``locale.getencoding`` reports (``getpreferredencoding`` on 3.10)."""
    if hasattr(locale, "getencoding"):
        return locale.getencoding()
    return locale.getpreferredencoding(False)


def _acp_is_multibyte() -> bool:
    """True when this machine's ANSI code page is multibyte (DBCS or UTF-8).

    That is exactly when the debugger truncates its text output over a pipe, so
    it is the gate for reading output from the Unicode log instead. Uses
    ``GetCPInfo(GetACP()).MaxCharSize`` - 1 for a single-byte page such as
    Western 1252, greater for 932/936/949/950/65001. False where the call is
    unavailable, so a single-byte or non-Windows host keeps the pipe path.
    """
    try:
        import ctypes

        class _CPINFO(ctypes.Structure):
            _fields_ = [
                ("MaxCharSize", ctypes.c_uint),
                ("DefaultChar", ctypes.c_char * 2),
                ("LeadByte", ctypes.c_char * 12),
            ]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        info = _CPINFO()
        if kernel32.GetCPInfo(kernel32.GetACP(), ctypes.byref(info)):
            return info.MaxCharSize > 1
    except Exception:
        pass
    return False


def _extract_log_output(segment: str) -> List[str]:
    """The debugger's own output lines from one command's Unicode-log segment.

    The segment runs from just after the previous command's marker up to (not
    including) the line that echoes this command's ``.echo <marker>``. Its first
    line is the echo of the command itself; both it and any other prompt-prefixed
    line are the transcript's scaffolding, not output, and are dropped. Leading
    and trailing blank lines (a bare prompt writes one) are trimmed so a command
    that prints nothing yields ``[]``, as the pipe path does.
    """
    lines = [ln.rstrip("\r") for ln in segment.split("\n")]
    kept = [ln for ln in lines if not _LOGGED_PROMPT.match(ln) and not _MARKER_LINE.fullmatch(ln)]
    while kept and kept[0] == "":
        kept.pop(0)
    while kept and kept[-1] == "":
        kept.pop()
    return kept


class DebuggerProcess:
    """A debugger subprocess plus the marker protocol used to drive it.

    Subclasses set ``is_live_session`` and provide their launch arguments and a
    ``_startup`` that reaches the first prompt. Everything else - the reader
    thread, ``send_command``, timeout handling, and shutdown - is shared.
    """

    #: Whether this attaches to a running target (remote/kernel) rather than a
    #: static dump. Live sessions get their own process group (so CTRL+BREAK can
    #: break in) and are detached with CTRL+B instead of quit with ``q``.
    is_live_session: bool = False

    #: Whether this session's debug engine is our own subprocess (a dump or a
    #: kernel target on the wire), rather than a remote server we are only a
    #: client of. The Unicode-log transport needs the engine local, since it
    #: opens and reads a log file on this machine. A -remote client sets False.
    _engine_is_local: bool = True

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
        #: The reader's current command buffer. It is published under the same
        #: lock as marker state so a timeout can retain output before the marker
        #: arrives, even though the reader owns the list itself.
        self._reader_buffer: Optional[List[str]] = None
        self._reader_truncated = False
        self._reader_recent = deque(maxlen=_EXIT_TAIL_LINES)
        self._log_limit_exceeded = False
        self._last_log_check = 0.0
        self._shutdown_lock = threading.Lock()
        self._release_thread: Optional[threading.Thread] = None
        self._release_done = threading.Event()
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
        self._marker_nonce = uuid.uuid4().hex
        self._expected_marker: Optional[str] = None
        #: True between a go-class command and the next break-in. While set, the
        #: debugger is not reading its input, so the marker protocol is unusable.
        self._target_running = False
        #: Set by shutdown so a parked wait stops rather than outliving the session.
        self._closing = False
        #: Set by the reader at EOF: the debugger is gone and no marker can land.
        self._debugger_exited = False

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
                # cdb/kd write output in the process ANSI code page (GetACP),
                # regardless of PYTHONUTF8; decode with the same one, and never
                # strictly, so a byte a multibyte code page split across reads
                # cannot raise in the reader thread and wedge the session.
                encoding=_debugger_output_encoding(),
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as e:  # pragma: no cover - Popen rarely fails once the exe is located
            raise DebuggerError(f"Failed to start debugger process: {e}")

        #: Unicode-log content channel (see _enable_unicode_log). Inactive until
        #: the log is open, and only ever opened on a multibyte code page.
        self._log_path: Optional[str] = None
        self._log_offset = 0
        self._log_active = False

        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

        self._startup()
        self._enable_unicode_log()

    # -- Subclass hooks ---------------------------------------------------

    def _on_output_line(self, line: str) -> None:
        """Called (under ``self.lock``) for every output line. Kernel uses it
        to notice the ``Connected to target`` banner."""

    def _on_debugger_exit(self) -> None:
        """Called by the reader once the debugger's output ends. Kernel uses it
        to stop waiting for a connect banner that can no longer come."""

    # -- Reader thread ----------------------------------------------------

    def _read_output(self) -> None:
        if not self.process or not self.process.stdout:
            return

        buffer = BoundedOutput()
        fragment = False
        with self.lock:
            self._reader_buffer = buffer.lines
        try:
            for line in output_lines(self.process.stdout):
                was_fragment, fragment = fragment, not line.endswith("\n")
                line = line.rstrip("\r\n")
                if self.verbose:
                    print(f"DBG > {line}", file=sys.stderr)
                self._check_log_size()
                with self.lock:
                    prompt = _LOGGED_PROMPT.match(line) if not was_fragment else None
                    payload = line[prompt.end():].lstrip(" \t") if prompt else line
                    self._on_output_line(line)
                    if not payload:
                        continue
                    if MARKER_BASE in payload and prompt and payload.startswith(".echo "):
                        continue  # The echoed input is not completion output.
                    if not was_fragment and not fragment and _MARKER_LINE.fullmatch(payload):
                        if self._expected_marker == payload:
                            self.output_lines = buffer.result()
                            buffer = BoundedOutput()
                            self._reader_buffer = buffer.lines
                            self._reader_truncated = False
                            self._expected_marker = None
                            self.ready_event.set()
                        continue  # Discard stale/foreign exact marker lines.
                    self._reader_recent.append(line[:1024])
                    buffer.append(line)
                    self._reader_truncated = buffer.truncated
        except (IOError, ValueError, AttributeError):
            if not self._closing:
                log_error("Debugger output reader failed", exc_info=True)
        finally:
            with self.lock:
                self._debugger_exited = True
                if self._expected_marker is not None or buffer.lines:
                    self.output_lines = buffer.result()
            self._on_debugger_exit()
            self.ready_event.set()

    def _check_log_size(self) -> None:
        if not self._log_active or time.monotonic() - self._last_log_check < 0.1:
            return
        self._last_log_check = time.monotonic()
        try:
            if os.path.getsize(self._log_path) > MAX_LOG_BYTES:
                with self.lock:
                    if self._log_limit_exceeded:
                        return
                    self._log_limit_exceeded = True
                threading.Thread(target=self._close_oversized_log, daemon=True).start()
                self.ready_event.set()
        except OSError:
            pass

    def _close_oversized_log(self) -> None:
        try:
            self.shutdown()
        except DebuggerError:
            pass  # shutdown logs the error and keeps ownership for retry.

    def _wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            self._check_log_size()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.ready_event.is_set()
            if self.ready_event.wait(min(0.1, remaining)):
                return True

    def _exited_message(self, doing: str, tail: List[str]) -> str:
        process = self.process
        code = None
        if process is not None:
            try:
                code = process.wait(timeout=2)
            except Exception:
                code = process.poll()
        if code is None:
            status = ""
        elif code == 0:
            status = " (exit code 0)"
        else:
            # Windows exit codes are often NTSTATUS values, e.g. 0xC0000005.
            status = f" (exit code 0x{code & 0xFFFFFFFF:08X})"
        message = f"Debugger process exited{status} {doing}."
        with self.lock:
            tail = list(self._reader_recent) or tail
        if tail:
            message += "\nLast debugger output:\n" + "\n".join(tail[-_EXIT_TAIL_LINES:])
        return message

    def _raise_if_exited(self, doing: str) -> None:
        """Raise DebuggerExitedError if the debugger exited with the current
        marker still pending - i.e. it can never land."""
        if self._log_limit_exceeded:
            self.shutdown()
            raise DebuggerError(
                "Unicode log exceeded the 8 MiB safety threshold; session closed. "
                "Narrow the command and reopen the session."
            )
        with self.lock:
            if not self._debugger_exited or self._expected_marker is None:
                return
            self._expected_marker = None
            tail, self.output_lines = self.output_lines, []
        raise DebuggerExitedError(self._exited_message(doing, tail))

    def _write_input(self, text: str, doing: str, failure: str) -> None:
        """Write to the debugger's stdin, reporting an exit rather than a bare
        I/O error when the write failed because the process is gone."""
        try:
            self.process.stdin.write(text)
            self.process.stdin.flush()
        except (IOError, ValueError, AttributeError) as e:
            self.reader_thread.join(timeout=1)  # let the reader observe EOF
            self._raise_if_exited(doing)
            raise DebuggerError(f"{failure}: {e}")

    # -- Command protocol -------------------------------------------------

    def _take_output(self) -> List[str]:
        """Detach and return whatever the reader has published."""
        with self.lock:
            result = self.output_lines.copy()
            self.output_lines = []
        return result

    def _snapshot_partial_output(self) -> List[str]:
        """Return bounded output read before the current marker arrived."""
        with self.lock:
            lines = list(self._reader_buffer or [])
            truncated = self._reader_truncated

        bounded: List[str] = []
        char_count = 0
        for line in lines:
            if len(bounded) >= MAX_PARTIAL_OUTPUT_LINES:
                truncated = True
                break
            remaining = MAX_PARTIAL_OUTPUT_CHARS - char_count
            if remaining <= 0:
                truncated = True
                break
            if len(line) + 1 > remaining:
                bounded.append(line[: max(0, remaining - 1)])
                truncated = True
                break
            bounded.append(line)
            char_count += len(line) + 1

        if truncated:
            bounded.append(
                "[partial output truncated; increase the command timeout or inspect the target directly]"
            )
        return bounded

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

    def _send_marked(
        self, command: str, cmd_timeout: int, preamble: Optional[List[str]] = None
    ) -> List[str]:
        """Write *command* with a completion marker and return its output."""
        marker = self._next_marker()
        self.ready_event.clear()
        with self.lock:
            self.output_lines = []
            self._expected_marker = marker
        doing = f"while running '{command}'"
        self._raise_if_exited(doing)
        # A bare .echo frames a fresh line even on CDB's truncated DBCS pipe.
        self._write_input(f"{command}\n.echo\n.echo {marker}\n", doing, "Failed to send command")

        landed = self._wait_ready(cmd_timeout)
        self._raise_if_exited(doing)
        if self._closing:
            raise DebuggerError("Session was closed while the command was running")
        if not landed and not self._marker_landed():
            partial_output = self._snapshot_partial_output()
            resynced = self._abort_running_command()
            detail = "" if resynced else " (session closed because command synchronization was lost; reopen it)"
            # The break-in output is the only record of why the target stopped
            # and would otherwise die with this exception, so it rides along.
            lost = (
                "\nThe target had stopped with:\n" + "\n".join(preamble)
                if preamble
                else ""
            )
            partial = (
                "\nPartial output before timeout:\n" + "\n".join(partial_output)
                if partial_output
                else ""
            )
            raise DebuggerError(
                f"Command timed out after {cmd_timeout} seconds: {command}{detail}{lost}{partial}",
                partial_output=partial_output,
            )

        pipe_output = self._take_output()
        if self._log_active:
            # The pipe truncates multibyte output; the log does not. Prefer the
            # log segment for this command, falling back to the pipe if the log
            # has not caught up (it always should, the marker just landed).
            logged = self._read_log_segment(marker)
            if logged is not None:
                self._rotate_log_if_needed()
                return logged
            # Do not reuse an offset that would include this command next time.
            self._log_active = False
            log_error("Unicode log boundary missing; falling back to the pipe")
        return pipe_output

    # -- Unicode log content channel --------------------------------------

    def _read_log_segment(self, marker: str) -> Optional[List[str]]:
        """This command's output from the Unicode log, or None if not ready.

        The marker appears twice in the log: first in the echoed ``.echo
        <marker>`` command, then as that command's output. Everything before the
        first is this command's transcript; consuming through the second leaves
        the offset at a clean boundary for the next command.
        """
        result = read_log_segment(self._log_path, self._log_offset, marker, self.timeout)
        if result is None:
            return None
        output, self._log_offset = result
        return output

    def _rotate_log_if_needed(self) -> None:
        if not self._log_active or os.path.getsize(self._log_path) <= ROTATE_LOG_BYTES:
            return
        self._log_active = False  # Avoid recursively rotating the .logclose command.
        self._send_marked(".logclose", self.timeout)
        self._cleanup_log()
        self._enable_unicode_log()

    def _cleanup_log(self) -> None:
        self._log_active = False
        if not self._log_path:
            return
        # A detached remote client is force-killed rather than quit, so the OS
        # may still be releasing its handle on the log file when we get here; a
        # dump/kernel session that quit cleanly releases it at once. Retry
        # briefly so the temp file is not leaked in the remote case.
        for _ in range(20):
            try:
                os.remove(self._log_path)
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.05)
        else:
            log_error("Could not remove Unicode log %s", self._log_path)
            raise DebuggerError(f"Could not remove Unicode log {self._log_path!r}; retry close")
        self._log_path = None

    def _release_target(self) -> None:
        """Request debugger exit before the client process is dropped.

        - A dump session quits with ``q``.
        - A live user-mode remote exits this client with CTRL+B; it does not
          promise to resume the independently managed server target.

        Kernel sessions override this: only ``g`` resumes a kernel target, so
        :class:`~mcp_windbg.kd_session.KDSession` handles its release policy.
        """
        if self.is_live_session:
            self.process.stdin.write("\x02")  # CTRL+B detaches a user-mode remote
        else:
            self.process.stdin.write("q\n")
        self.process.stdin.flush()

    def _release_gracefully(self) -> None:
        try:
            self._release_target()
        except Exception:
            pass  # A broken/full stdin pipe must not prevent the tree kill.
        finally:
            self._release_done.set()

    def shutdown(self) -> None:
        """Request client exit, then terminate the debugger process.

        Deliberately does not take ``_io_lock``: closing a session has to work
        while a ``wait_for_break`` is parked on it, which is precisely when the
        lock is held. Instead it flags the session closed and wakes the waiter,
        which then reports the close rather than sitting out its full timeout on
        a debugger that no longer exists.
        """
        self._closing = True
        self.ready_event.set()
        with self._shutdown_lock:
            try:
                if self.process and self.process.poll() is None:
                    if self._release_thread is None or not self._release_thread.is_alive():
                        self._release_done.clear()
                        self._release_thread = threading.Thread(target=self._release_gracefully, daemon=True)
                        self._release_thread.start()
                    if self._release_done.wait(_SHUTDOWN_GRACE_SECONDS):
                        try:
                            self.process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
                        except Exception:
                            pass
                    if self.process.poll() is None:
                        self._terminate_process()
                if self.process:
                    if self._release_thread is not None:
                        self._release_thread.join(timeout=2)
                        if self._release_thread.is_alive():
                            raise DebuggerError("Debugger release writer did not stop; retry close")
                    self.reader_thread.join(timeout=2)
                    if self.reader_thread.is_alive():
                        raise DebuggerError("Debugger reader did not stop; retry close")
                    for pipe in (self.process.stdin, self.process.stdout):
                        close = getattr(pipe, "close", None)
                        if close:
                            close()
                    self.process = None
                self._cleanup_log()
            except Exception as error:
                log_error("Debugger shutdown failed; ownership retained for retry", exc_info=True)
                pid = f" PID {self.process.pid}" if self.process else ""
                raise DebuggerError(f"Debugger{pid} shutdown failed; retry close: {error}") from error

    def _terminate_process(self) -> None:
        """Kill the debugger process. On Windows use a tree kill: cdb.exe/kd.exe
        launched via the Microsoft Store execution aliases spawn a child that a
        plain terminate() leaves behind holding the target/connection."""
        process = self.process
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=8, stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                raise DebuggerError(f"taskkill failed for PID {process.pid} (exit {result.returncode}); retry close")
        else:  # pragma: no cover - project is Windows-only
            process.terminate()
        process.wait(timeout=3)
        if process.poll() is None:
            raise DebuggerError(f"Debugger PID {process.pid} is still alive; retry close")

    def __enter__(self):  # pragma: no cover - convenience API, not used by the server
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # pragma: no cover
        self.shutdown()
