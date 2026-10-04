"""Debugger subprocess, output reader, Unicode log and resource ownership."""
from __future__ import annotations

import locale
import os
import re
import subprocess
import threading
import time
import uuid
from typing import List, Optional

MARKER_BASE = "COMMAND_COMPLETED_MARKER"
_MARKER_LINE = re.compile(rf"{MARKER_BASE}_(?:[0-9a-f]{{32}}_)?\d+")
_EXIT_TAIL_LINES = 20
_LOGGED_PROMPT = re.compile(r"^(?:\[.*\]\s*)?(?:\d+:[^>]*|l?kd)>")

MAX_PARTIAL_OUTPUT_LINES = 2000


MAX_PARTIAL_OUTPUT_CHARS = 64 * 1024


class DebuggerError(Exception):
    """Raised for any debugger session failure (launch, timeout, I/O)."""

    def __init__(self, message: str, *, partial_output: Optional[List[str]] = None):
        super().__init__(message)
        self.partial_output = list(partial_output or [])


class DebuggerExitedError(DebuggerError):
    """The debugger process exited; the session cannot be used again."""


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
    kept = [ln for ln in lines if not _LOGGED_PROMPT.match(ln)]
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

        buffer: List[str] = []
        with self.lock:
            self._reader_buffer = buffer
        try:
            for line in self.process.stdout:
                line = line.rstrip()
                if self.verbose:
                    print(f"DBG > {line}")

                with self.lock:
                    if MARKER_BASE in line and _LOGGED_PROMPT.match(line):
                        # Remote transcripts echo another client's command too;
                        # an echoed command is not its completion output.
                        self._on_output_line(line)
                        continue
                    if _MARKER_LINE.fullmatch(line):
                        # Drop abandoned/foreign markers, but only the exact
                        # standalone output of our pending marker completes it.
                        self._on_output_line(line)
                        if self._expected_marker == line:
                            self.output_lines = buffer
                            buffer = []
                            self._reader_buffer = buffer
                            self._expected_marker = None
                            self.ready_event.set()
                        continue
                    buffer.append(line)
                    self._on_output_line(line)
        except (IOError, ValueError, AttributeError) as e:
            if self.verbose:
                print(f"Debugger output reader error: {e}")
        finally:
            # Publish what the debugger printed last - often the only record of
            # why it exited - and wake any waiter rather than let it time out.
            with self.lock:
                self._debugger_exited = True
                self.output_lines = buffer
            self._on_debugger_exit()
            self.ready_event.set()

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
        if tail:
            message += "\nLast debugger output:\n" + "\n".join(tail[-_EXIT_TAIL_LINES:])
        return message

    def _raise_if_exited(self, doing: str) -> None:
        """Raise DebuggerExitedError if the debugger exited with the current
        marker still pending - i.e. it can never land."""
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

        bounded: List[str] = []
        char_count = 0
        truncated = False
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
        self._write_input(f"{command}\n.echo {marker}\n", doing, "Failed to send command")

        landed = self.ready_event.wait(cmd_timeout)
        if self._closing:
            raise DebuggerError("Session was closed while the command was running")
        self._raise_if_exited(doing)
        if not landed and not self._marker_landed():
            partial_output = self._snapshot_partial_output()
            resynced = self._abort_running_command()
            detail = "" if resynced else " (session may need a manual break-in)"
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
                return logged
        return pipe_output

    # -- Unicode log content channel --------------------------------------

    def _read_log_segment(self, marker: str) -> Optional[List[str]]:
        """This command's output from the Unicode log, or None if not ready.

        The marker appears twice in the log: first in the echoed ``.echo
        <marker>`` command, then as that command's output. Everything before the
        first is this command's transcript; consuming through the second leaves
        the offset at a clean boundary for the next command.
        """
        deadline = time.time() + max(2.0, self.timeout / 10)
        while True:
            try:
                with open(self._log_path, "rb") as handle:
                    handle.seek(self._log_offset)
                    text = handle.read().decode("utf-16-le", errors="replace")
            except OSError:
                return None
            offset = 0
            command_start = None
            for line in text.splitlines(keepends=True):
                content = line.rstrip("\r\n")
                prompt = _LOGGED_PROMPT.match(content)
                if prompt and content[prompt.end():].strip() == f".echo {marker}":
                    command_start = offset
                elif content == marker and line.endswith("\n") and command_start is not None:
                    end = offset + len(line)
                    self._log_offset += len(text[:end].encode("utf-16-le"))
                    return _extract_log_output(text[:command_start])
                offset += len(line)
            if time.time() >= deadline:
                return None
            time.sleep(0.02)

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
        self._log_path = None

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
            self._cleanup_log()

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
