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
from typing import List, Optional

from .debug_session import (
    DebuggerError,
    DebuggerSession,
    build_debugger_args,
    find_executable,
)

# Kept as the public error name for user-mode sessions.
CDBError = DebuggerError

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


def build_cdb_args(
    cdb_path: str,
    dump_path: Optional[str] = None,
    remote_connection: Optional[str] = None,
    kernel_connection: Optional[str] = None,
    symbols_path: Optional[str] = None,
    additional_args: Optional[List[str]] = None,
) -> List[str]:
    """Thin wrapper over :func:`build_debugger_args` (kept for callers/tests)."""
    return build_debugger_args(
        cdb_path,
        dump_path=dump_path,
        remote_connection=remote_connection,
        kernel_connection=kernel_connection,
        symbols_path=symbols_path,
        additional_args=additional_args,
    )


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
