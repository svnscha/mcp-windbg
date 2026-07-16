"""Kernel debugging session driven by ``kd.exe``.

Kernel debugging is separate from user-mode (:mod:`cdb_session`) for two
reasons: it needs ``kd.exe`` (``cdb.exe`` cannot drive a kernel cable), and its
startup is different. Where a dump or ``-remote`` session is usable the moment
the debugger prints its first prompt, a kernel target attaches asynchronously:
``kd`` prints ``Connected to target ...`` once the KDNET/COM link comes up, and
only then can we break in. So ``_startup`` here waits for that banner, sends
CTRL+BREAK to halt the target, and waits for the resulting prompt.

Connection strings (passed to ``-k`` verbatim):

- KDNET (network):   ``net:port=50000,key=1.2.3.4``
- Named pipe (VM):   ``com:pipe,port=\\\\.\\pipe\\com_1,baud=115200,reconnect,resets=0``
- Serial:            ``com:port=COM1,baud=115200``
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Optional

from .debug_session import (
    DebuggerError,
    DebuggerSession,
    build_debugger_args,
    find_executable,
)

# Raised for kernel session failures.
KDError = DebuggerError

# The banner ``kd`` prints once the kernel link is up (KDNET or COM). Matched as
# a substring; the full line is e.g.
#   "Connected to target 172.16.2.189 on port 50005 on local IP 172.16.2.183."
KERNEL_CONNECTED_BANNER = "Connected to target"

# Default paths where kd.exe (kernel debugger) might be located.
DEFAULT_KD_PATHS = [
    # Traditional Windows SDK locations
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\kd.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\kd.exe",
    r"C:\Program Files\Debugging Tools for Windows (x64)\kd.exe",
    r"C:\Program Files\Debugging Tools for Windows (x86)\kd.exe",

    # Microsoft Store WinDbg locations
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\kdX64.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\kdX86.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\kdARM64.exe"),
]


class KDSession(DebuggerSession):
    """A kernel ``kd.exe`` session attached with ``-k``."""

    is_live_session = True

    def __init__(
        self,
        kernel_connection: str,
        kd_path: Optional[str] = None,
        symbols_path: Optional[str] = None,
        timeout: int = 60,
        verbose: bool = False,
    ):
        """Attach to a kernel target.

        Args:
            kernel_connection: The ``-k`` connection string (KDNET/pipe/serial).
            kd_path: Custom kd.exe path; auto-discovered when None.
            symbols_path: Extra symbol search path.
            timeout: Seconds to wait for the target to connect and break in.
            verbose: Echo debugger output for debugging.

        Raises:
            KDError: kd.exe not found, or the target never connected / broke in.
            ValueError: no connection string provided.
        """
        if not kernel_connection:
            raise ValueError("kernel_connection must be provided")

        self.kernel_connection = kernel_connection
        # Set before super().__init__ starts the reader thread, which references it.
        self._connected_event = threading.Event()

        kd_path = find_executable(DEFAULT_KD_PATHS, kd_path)
        if not kd_path:
            raise KDError(
                "Could not find kd.exe for kernel debugging. "
                "Please install Debugging Tools for Windows."
            )
        self.kd_path = kd_path

        launch_args = build_debugger_args(
            kd_path,
            kernel_connection=kernel_connection,
            symbols_path=symbols_path,
        )

        super().__init__(
            debugger_path=kd_path,
            launch_args=launch_args,
            timeout=timeout,
            verbose=verbose,
        )

    def _on_output_line(self, line: str) -> None:
        """Notice the connect banner (called under the reader lock)."""
        if KERNEL_CONNECTED_BANNER in line:
            self._connected_event.set()

    def _startup(self) -> None:
        """Wait for the target to connect, break in, then reach the prompt."""
        if not self._connected_event.wait(self.timeout):
            self.shutdown()
            raise KDError(
                "Timed out waiting for the kernel target to connect. "
                "Is the target booted with debugging enabled and transmitting "
                "on this transport? (kd reports 'no_debuggee' until it is.)"
            )
        # Target is connected but running; break in to reach a prompt.
        try:
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception as e:  # pragma: no cover - signal delivery rarely fails
            self.shutdown()
            raise KDError(f"Failed to break into the kernel target: {e}")
        try:
            self._wait_for_prompt(self.timeout)
        except DebuggerError:
            self.shutdown()
            raise KDError("Kernel debugger did not reach a prompt after break-in")
