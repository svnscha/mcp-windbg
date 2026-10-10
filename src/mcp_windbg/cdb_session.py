"""User-mode debugging session driven by ``cdb.exe``.

Handles the two user-mode attach modes:

- a crash **dump** (``-z``), a static target, and
- a user-mode **remote** debug server (``-remote``), a live target.

Kernel debugging lives in :mod:`kd_session` (it needs ``kd.exe`` and a different
connect handshake). The shared subprocess/marker machinery is in
:mod:`debug_session`.
"""

from __future__ import annotations

import os
import threading
from typing import List, Optional

from .debug_session import (
    DebuggerError,
    DebuggerExitedError,
    DebuggerSession,
    build_debugger_args,
    find_executable,
)

# Kept as the public error name for user-mode sessions.
CDBError = DebuggerError

# What a -remote client prints once it is attached to the debug server. It is
# the only thing a connect reliably produces: a stopped target follows it with
# a prompt, a running one with silence. Printed by cdb.exe itself, not dbgeng,
# and unchanged across the 10.0.26100 and 10.0.28000 debuggers.
REMOTE_CONNECTED_BANNER = "Connected to server with"

# How long an attached -remote client gives the target to answer the prompt
# probe before concluding it is running. A stopped target answers in one
# round trip (about 0.1s on localhost); a running one never does, because the
# server is not reading commands while the target has the CPU. Only paid
# after the connect banner, so connection latency does not count against it.
REMOTE_PROMPT_PROBE_TIMEOUT = 2

# Default paths where cdb.exe might be located.
DEFAULT_CDB_PATHS = [
    # Traditional Windows SDK locations
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x64)\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x86)\cdb.exe",

    # Microsoft Store WinDbg locations (architecture-specific)
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX64.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX86.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbARM64.exe"),
]


class CDBSession(DebuggerSession):
    """A user-mode ``cdb.exe`` session over a dump or a ``-remote`` server."""

    def __init__(
        self,
        dump_path: Optional[str] = None,
        remote_connection: Optional[str] = None,
        cdb_path: Optional[str] = None,
        symbols_path: Optional[str] = None,
        timeout: int = 60,
        verbose: bool = False,
        additional_args: Optional[List[str]] = None,
        auto_dump_dir_symbols: bool = True,
    ):
        """Start a user-mode session.

        Args:
            dump_path: Crash dump to open (``-z``), mutually exclusive with remote.
            remote_connection: User-mode debug server string (``-remote``).
            cdb_path: Custom cdb.exe path; auto-discovered when None.
            symbols_path: Extra symbol search path.
            timeout: Seconds to wait for the debugger to become ready.
            verbose: Echo debugger output for debugging.
            additional_args: Extra cdb.exe arguments.
            auto_dump_dir_symbols: Prepend the dump's directory to the symbol path.

        Raises:
            CDBError: cdb.exe not found or failed to start / initialize.
            FileNotFoundError: the dump file does not exist.
            ValueError: neither or both attach sources provided.
        """
        provided = [c for c in (dump_path, remote_connection) if c]
        if not provided:
            raise ValueError("Either dump_path or remote_connection must be provided")
        if len(provided) > 1:
            raise ValueError("dump_path and remote_connection are mutually exclusive")

        if dump_path and not os.path.isfile(dump_path):
            raise FileNotFoundError(f"Dump file not found: {dump_path}")

        self.dump_path = dump_path
        self.remote_connection = remote_connection
        self.is_live_session = bool(remote_connection)
        # A -remote client drives a debug engine on the server, so .logopen would
        # open the Unicode log on the server (a path/lifecycle we do not own).
        # The log-output transport is only for sessions whose engine is ours.
        self._engine_is_local = remote_connection is None
        # Set before super().__init__ starts the reader thread, which references it.
        self._connected_event = threading.Event()

        cdb_path = find_executable(DEFAULT_CDB_PATHS, cdb_path)
        if not cdb_path:
            raise CDBError("Could not find cdb.exe. Please provide a valid path.")
        self.cdb_path = cdb_path

        # Auto-include the dump's own directory in the symbol search path.
        if auto_dump_dir_symbols and dump_path:
            dump_dir = os.path.dirname(os.path.abspath(dump_path))
            symbols_path = f"{dump_dir};{symbols_path}" if symbols_path else dump_dir

        launch_args = build_debugger_args(
            cdb_path,
            dump_path=dump_path,
            remote_connection=remote_connection,
            symbols_path=symbols_path,
            additional_args=additional_args,
        )

        super().__init__(
            debugger_path=cdb_path,
            launch_args=launch_args,
            timeout=timeout,
            verbose=verbose,
        )

    def _on_output_line(self, line: str) -> None:
        """Notice the remote connect banner (called under the reader lock)."""
        if REMOTE_CONNECTED_BANNER in line:
            self._connected_event.set()

    def _on_debugger_exit(self) -> None:
        self._connected_event.set()

    def _startup(self) -> None:
        """Reach the first prompt, or establish that the target is running.

        A dump answers as soon as cdb has loaded it, so the base probe is the
        whole story. A ``-remote`` client is different: being attached to the
        debug server says nothing about the target, which is running whenever
        the server's operator resumed it before we connected. A running target
        reads no input, so a probe never answers, and waiting the full timeout
        for it reported "initialization timed out" for a session that was
        fine. Instead: wait for the connect banner, which is where connection
        latency belongs, then give the target one short window to answer.
        Silence means it is running, and the session opens in that state. The
        first ordinary command breaks in, exactly as it does after ``g``.
        """
        if not self.is_live_session:
            super()._startup()
            return
        if not self._connected_event.wait(self.timeout):
            self.shutdown()
            raise CDBError("Timed out connecting to the debug server")
        if self._debugger_exited:
            message = self._exited_message(
                "while connecting to the debug server", self._take_output()
            )
            self.shutdown()
            raise DebuggerExitedError(message)
        try:
            self._wait_for_prompt(REMOTE_PROMPT_PROBE_TIMEOUT)
        except DebuggerExitedError:
            self.shutdown()
            raise
        except DebuggerError:
            # The probe's .echo stays queued at the server and prints when
            # the target next stops; the reader drops it as a stray marker.
            if not self._abandon_marker():
                self._target_running = True
                # What the reader holds is the connect banner and the
                # server's replayed transcript, which a landed probe would
                # have swept away. Drop it the same way, so the break-in
                # reports only why the target stopped.
                with self.lock:
                    if self._reader_buffer is not None:
                        self._reader_buffer.clear()
