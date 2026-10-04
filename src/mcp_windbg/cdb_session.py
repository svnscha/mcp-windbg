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
import re
import threading
import time
from typing import List, Optional

from .debug_output import BoundedOutput, LOGGED_PROMPT
from .debug_session import (
    DebuggerError,
    DebuggerExitedError,
    DebuggerContextError,
    DebuggerSession,
    build_debugger_args,
    find_executable,
)

# Kept as the public error name for user-mode sessions.
CDBError = DebuggerError

# First register in the default x86/x64/ARM/ARM64/IA64 register display.
_REGISTER_CONTEXT = re.compile(r"^\s*(?:eax|rax|r0|x0|pc|ip|eip|rip|iip)\s*=\s*[0-9a-f]+", re.I)

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
        self._requires_remote_context = self.is_live_session
        self._remote_context_ready = threading.Event()
        self._remote_probe_pending = False
        self._remote_probe_output = BoundedOutput()
        # A -remote client drives a debug engine on the server, so .logopen would
        # open the Unicode log on the server (a path/lifecycle we do not own).
        # The log-output transport is only for sessions whose engine is ours.
        self._engine_is_local = remote_connection is None

        cdb_path = find_executable(DEFAULT_CDB_PATHS, cdb_path)
        if not cdb_path:
            raise CDBError("Could not find cdb.exe. Please provide a valid path.")
        self.cdb_path = cdb_path

        # Auto-include the dump's own directory in the symbol search path.
        if auto_dump_dir_symbols and dump_path:
            # -y replaces the inherited path; keep it when no explicit path was supplied.
            symbols_path = symbols_path or os.environ.get("_NT_SYMBOL_PATH")
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

    def _startup(self) -> None:
        """A remote client's marker proves connectivity, not a stopped target."""
        if not self.is_live_session:
            super()._startup()
            return
        deadline = time.monotonic() + self.timeout
        try:
            super()._startup()
            self._take_output()
            # The open tool promises initial triage, which needs a thread context.
            # A running -remote client can answer a bare .echo without one.
            self.send_ctrl_break()
            self._wait_for_target_context(max(0.001, deadline - time.monotonic()))
            self._take_output()
            self._target_running = False
        except BaseException:
            self.shutdown()
            raise

    def _on_output_line(self, line: str) -> None:
        if not self._remote_probe_pending:
            return
        prompt = LOGGED_PROMPT.match(line)
        if prompt:
            line = line[prompt.end():].lstrip(" \t")
        if _REGISTER_CONTEXT.match(line):
            self._remote_probe_pending = False
            self._remote_context_ready.set()
        elif "does not have a current" in line:
            # A warning is a completed but invalid probe. Retry only after that
            # reply; a queued r with no reply must never be duplicated.
            self._remote_probe_pending = False

    def _on_debugger_exit(self) -> None:
        self._remote_context_ready.set()

    def _wait_for_target_context(self, timeout: float) -> None:
        if not self._requires_remote_context:
            super()._wait_for_target_context(timeout)
            return
        deadline = time.monotonic() + timeout
        if not self._remote_probe_pending:
            self._remote_context_ready.clear()
        while True:
            if self._closing:
                raise CDBError("Session was closed while checking remote context")
            if self._debugger_exited:
                raise DebuggerExitedError(self._exited_message("while checking remote context", self._take_output()))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = "\n".join(self._remote_probe_output.result()[-20:])
                raise DebuggerContextError("Remote debugger did not provide a thread context after CTRL+BREAK\n" + detail)
            if not self._remote_probe_pending and not self._remote_context_ready.is_set():
                self._remote_probe_pending = True
                self._write_input("r\n", "while checking remote context", "Failed to query registers")
                self._wait_for_prompt(remaining)
                for line in self._take_output():
                    self._remote_probe_output.append(line)
            if self._remote_context_ready.is_set():
                self._wait_for_prompt(max(0.001, deadline - time.monotonic()))
                for line in self._take_output():
                    self._remote_probe_output.append(line)
                with self.lock:
                    self.output_lines = self._remote_probe_output.result()
                self._remote_probe_output = BoundedOutput()
                return
            self._remote_context_ready.wait(min(0.05, max(0, deadline - time.monotonic())))
