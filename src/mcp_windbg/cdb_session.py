import subprocess
import threading
import re
import os
import platform
import signal
from typing import List, Optional

# Regular expression to detect CDB prompts
PROMPT_REGEX = re.compile(r"^\d+:\d+>\s*$")

# Command marker to reliably detect command completion
COMMAND_MARKER = ".echo COMMAND_COMPLETED_MARKER"
COMMAND_MARKER_PATTERN = re.compile(r"COMMAND_COMPLETED_MARKER")

# Default paths where cdb.exe might be located
DEFAULT_CDB_PATHS = [
    # Traditional Windows SDK locations
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x64)\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x86)\cdb.exe",

    # Microsoft Store WinDbg Preview locations (architecture-specific)
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX64.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX86.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbARM64.exe")
]

# Default paths where kd.exe (kernel debugger) might be located
DEFAULT_KD_PATHS = [
    # Traditional Windows SDK locations
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\kd.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\kd.exe",
    r"C:\Program Files\Debugging Tools for Windows (x64)\kd.exe",
    r"C:\Program Files\Debugging Tools for Windows (x86)\kd.exe",

    # Microsoft Store WinDbg Preview locations
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\kdX64.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\kdX86.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\kdARM64.exe")
]

class CDBError(Exception):
    """Custom exception for CDB-related errors"""
    pass


def build_cdb_args(
    cdb_path: str,
    dump_path: Optional[str] = None,
    remote_connection: Optional[str] = None,
    kernel_connection: Optional[str] = None,
    symbols_path: Optional[str] = None,
    additional_args: Optional[List[str]] = None,
) -> List[str]:
    """Assemble the cdb.exe command line for a session.

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
    cmd_args = [cdb_path]

    if dump_path:
        cmd_args.extend(["-z", dump_path])
    elif remote_connection:
        cmd_args.extend(["-remote", remote_connection])
    elif kernel_connection:  # pragma: no cover - kernel attach needs a live target; covered by test_build_cdb_args and tests/e2e/manual-verification.md
        cmd_args.extend(["-k", kernel_connection])

    if symbols_path:
        cmd_args.extend(["-y", symbols_path])

    if additional_args:  # pragma: no cover - not used by the server; covered by test_build_cdb_args
        cmd_args.extend(additional_args)

    return cmd_args


class CDBSession:
    def __init__(
        self,
        dump_path: Optional[str] = None,
        remote_connection: Optional[str] = None,
        kernel_connection: Optional[str] = None,
        cdb_path: Optional[str] = None,
        symbols_path: Optional[str] = None,
        initial_commands: Optional[List[str]] = None,
        timeout: int = 10,
        verbose: bool = False,
        additional_args: Optional[List[str]] = None,
        auto_dump_dir_symbols: bool = True
    ):
        """
        Initialize a new CDB debugging session.

        Args:
            dump_path: Path to the crash dump file (mutually exclusive with the connection args)
            remote_connection: User-mode remote debug server string (e.g., "tcp:Port=5005,Server=192.168.0.100"), launched with -remote
            kernel_connection: Kernel debug connection string (e.g., "net:port=50000,key=..."), launched with -k
            cdb_path: Custom path to cdb.exe. If None, will try to find it automatically
            symbols_path: Custom symbols path. If None, uses default Windows symbols
            initial_commands: List of commands to run when CDB starts
            timeout: Timeout in seconds for waiting for CDB responses
            verbose: Whether to print additional debug information
            additional_args: Additional arguments to pass to cdb.exe

        Raises:
            CDBError: If cdb.exe cannot be found or started
            FileNotFoundError: If the dump file cannot be found
            ValueError: If invalid parameters are provided
        """
        # Validate that exactly one connection source is provided
        provided = [c for c in (dump_path, remote_connection, kernel_connection) if c]
        if not provided:
            raise ValueError("Either dump_path, remote_connection, or kernel_connection must be provided")
        if len(provided) > 1:
            raise ValueError("dump_path, remote_connection, and kernel_connection are mutually exclusive")

        if dump_path and not os.path.isfile(dump_path):
            raise FileNotFoundError(f"Dump file not found: {dump_path}")

        self.dump_path = dump_path
        self.remote_connection = remote_connection
        self.kernel_connection = kernel_connection
        self.timeout = timeout
        self.verbose = verbose
        self.cdb_path = None  # Will be set by _find_cdb_executable() or _find_kd_executable()
        self.debugger_path = None

        # Find appropriate debugger: kd.exe for kernel, cdb.exe for user-mode
        if self.kernel_connection:
            self.debugger_path = self._find_kd_executable()
            if not self.debugger_path:
                raise CDBError("Could not find kd.exe for kernel debugging. Please install Debugging Tools for Windows.")
        else:
            self.cdb_path = self._find_cdb_executable(cdb_path)
            self.debugger_path = self.cdb_path
            if not self.debugger_path:
                raise CDBError("Could not find cdb.exe. Please provide a valid path.")

        # Auto-include dump file's directory in symbol search path
        if auto_dump_dir_symbols and self.dump_path:
            dump_dir = os.path.dirname(os.path.abspath(self.dump_path))
            if symbols_path:
                symbols_path = f"{dump_dir};{symbols_path}"
            else:
                symbols_path = dump_dir

        cmd_args = build_cdb_args(
            self.debugger_path,
            dump_path=self.dump_path,
            remote_connection=self.remote_connection,
            kernel_connection=self.kernel_connection,
            symbols_path=symbols_path,
            additional_args=additional_args,
        )

        try:
            # Only create a new process group for live sessions where CTRL+BREAK is needed
            creationflags = 0
            if os.name == 'nt' and self.is_live_session:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                cmd_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as e:  # pragma: no cover - Popen rarely fails once cdb is located
            raise CDBError(f"Failed to start CDB process: {str(e)}")

        self.output_lines = []
        self.lock = threading.Lock()
        self.ready_event = threading.Event()
        self._kernel_connected_event = threading.Event() if self.kernel_connection else None
        self.reader_thread = threading.Thread(target=self._read_output)
        self.reader_thread.daemon = True
        self.reader_thread.start()

        # Wait for CDB to initialize by sending an echo marker
        try:
            if self.kernel_connection:
                self._wait_for_kernel_prompt(timeout=self.timeout)
            else:
                self._wait_for_prompt(timeout=self.timeout)
        except CDBError:
            self.shutdown()
            raise CDBError("CDB initialization timed out")

        # Run initial commands if provided
        if initial_commands:  # pragma: no cover - not used by the server
            for cmd in initial_commands:
                self.send_command(cmd)

    @property
    def is_live_session(self) -> bool:
        """True for a live target (user-mode remote or kernel), as opposed to a dump.

        Live sessions get their own process group so CTRL+BREAK can break in, and
        are detached with CTRL+B on shutdown rather than quit with 'q'.
        """
        return bool(self.remote_connection or self.kernel_connection)

    def _find_cdb_executable(self, custom_path: Optional[str] = None) -> Optional[str]:
        """Find the cdb.exe executable"""
        if custom_path and os.path.isfile(custom_path):
            return custom_path

        for path in DEFAULT_CDB_PATHS:
            if os.path.isfile(path):
                return path

        return None

    def _find_kd_executable(self) -> Optional[str]:
        """Find the kd.exe (kernel debugger) executable"""
        for path in DEFAULT_KD_PATHS:
            if os.path.isfile(path):
                return path

        return None

    def _read_output(self):
        """Thread function to continuously read CDB output"""
        if not self.process or not self.process.stdout:
            return

        buffer = []
        try:
            for line in self.process.stdout:
                line = line.rstrip()
                if self.verbose:
                    print(f"CDB > {line}")

                with self.lock:
                    buffer.append(line)
                    if self._kernel_connected_event and "Connected to target" in line:
                        self._kernel_connected_event.set()
                    # Check if the marker is in this line
                    if COMMAND_MARKER_PATTERN.search(line):
                        # Remove the marker line itself
                        if buffer and COMMAND_MARKER_PATTERN.search(buffer[-1]):
                            buffer.pop()
                        self.output_lines = buffer
                        buffer = []
                        self.ready_event.set()
        except (IOError, ValueError) as e:
            if self.verbose:
                print(f"CDB output reader error: {e}")

    def _wait_for_kernel_prompt(self, timeout=None):
        """For kernel sessions: wait for target connection, break in, then wait for prompt."""
        t = timeout or self.timeout
        if not self._kernel_connected_event.wait(timeout=t):
            raise CDBError("Timed out waiting for kernel target to connect")
        # Target is running; send CTRL+BREAK to halt it and get the debugger prompt
        self.process.send_signal(signal.CTRL_BREAK_EVENT)
        self._wait_for_prompt(timeout=t)

    def _wait_for_prompt(self, timeout=None):
        """Wait for CDB to be ready for commands by sending a marker"""
        try:
            self.ready_event.clear()
            self.process.stdin.write(f"{COMMAND_MARKER}\n")
            self.process.stdin.flush()

            if not self.ready_event.wait(timeout=timeout or self.timeout):
                raise CDBError(f"Timed out waiting for CDB prompt")
        except IOError as e:
            raise CDBError(f"Failed to communicate with CDB: {str(e)}")

    def send_command(self, command: str, timeout: Optional[int] = None) -> List[str]:
        """
        Send a command to CDB and return the output

        Args:
            command: The command to send
            timeout: Custom timeout for this command (overrides instance timeout)

        Returns:
            List of output lines from CDB

        Raises:
            CDBError: If the command times out or CDB is not responsive
        """
        if not self.process:
            raise CDBError("CDB process is not running")

        self.ready_event.clear()
        with self.lock:
            self.output_lines = []

        try:
            # Send the command followed by our marker to detect completion
            self.process.stdin.write(f"{command}\n{COMMAND_MARKER}\n")
            self.process.stdin.flush()
        except IOError as e:
            raise CDBError(f"Failed to send command: {str(e)}")

        cmd_timeout = timeout or self.timeout
        if not self.ready_event.wait(timeout=cmd_timeout):
            raise CDBError(f"Command timed out after {cmd_timeout} seconds: {command}")

        with self.lock:
            result = self.output_lines.copy()
            self.output_lines = []
        return result

    def shutdown(self):
        """Clean up and terminate the CDB process"""
        try:
            if self.process and self.process.poll() is None:
                try:
                    if self.is_live_session:
                        # For live targets (remote/kernel), send CTRL+B to detach
                        self.process.stdin.write("\x02")  # CTRL+B
                        self.process.stdin.flush()
                    else:
                        # For dump files, send 'q' to quit
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

    def send_ctrl_break(self) -> None:
        """Send a CTRL+BREAK event to the CDB process to break in.

        Raises:
            CDBError: If the signal cannot be delivered or the process is not running.
        """
        if not self.process or self.process.poll() is not None:
            raise CDBError("CDB process is not running")

        try:
            # On Windows, deliver CTRL+BREAK to the new process group we created
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception as e:
            raise CDBError(f"Failed to send CTRL+BREAK: {str(e)}")

    def __enter__(self):  # pragma: no cover - convenience API, not used by the server
        """Support for context manager protocol"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # pragma: no cover - convenience API, not used by the server
        """Clean up when exiting context manager"""
        self.shutdown()
