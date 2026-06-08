"""Support infrastructure for the declarative end-to-end scenarios.

This module is written once and shared by every scenario. It locates ``cdb.exe``,
resolves the placeholders scenarios use (``{dump}``, ``{dumps_dir}``, ``{remote}``),
and can bring up a local CDB remote server for the remote-debugging scenarios.

Nothing here encodes a single test case: the cases live in the YAML files.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from mcp_windbg.cdb_session import DEFAULT_CDB_PATHS

# Directory layout (this file lives in src/mcp_windbg/tests/e2e/).
E2E_DIR = Path(__file__).resolve().parent
TESTS_DIR = E2E_DIR.parent
DUMPS_DIR = TESTS_DIR / "dumps"
SCENARIOS_DIR = TESTS_DIR / "scenarios"


def find_cdb() -> Optional[str]:
    """Return the path to a usable cdb.exe, or None when none is installed."""
    for path in DEFAULT_CDB_PATHS:
        if os.path.isfile(path):
            return path
    return None


def cdb_available() -> bool:
    return find_cdb() is not None


def server_command(server_args: list[str]) -> tuple[str, list[str]]:
    """Build the (command, args) that hosts the MCP server as a subprocess.

    When ``MCP_WINDBG_COVERAGE`` is set, the server runs under
    ``coverage run --parallel-mode`` so the hosted process - where all the tool
    dispatch actually executes - is measured. Each subprocess writes its own
    ``.coverage.*`` data file; ``coverage combine`` merges them afterwards.
    """
    module_args = ["-m", "mcp_windbg", *server_args]
    if os.environ.get("MCP_WINDBG_COVERAGE"):
        return sys.executable, ["-m", "coverage", "run", "--parallel-mode", *module_args]
    return sys.executable, module_args


def dump_file(name: str) -> Path:
    """Absolute path to a dump in the shared dumps directory (may not exist)."""
    return DUMPS_DIR / name


def free_port() -> int:
    """Ask the OS for an unused TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, timeout: float) -> bool:
    """Poll until something accepts connections on the port (HTTP server ready)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def resolve_placeholders(value: Any, mapping: dict[str, str]) -> Any:
    """Recursively replace ``{name}`` placeholders in scenario arguments."""
    if isinstance(value, str):
        for key, replacement in mapping.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, list):
        return [resolve_placeholders(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: resolve_placeholders(item, mapping) for key, item in value.items()}
    return value


class RemoteCdbServer:
    """A local ``cdb`` instance running ``.server tcp:port=N`` for remote tests.

    The bring-up logic mirrors what a user does by hand: start cdb on a target,
    wait for the initial prompt, then open a TCP server. ``connection_string``
    is what a scenario's ``{remote}`` placeholder resolves to.
    """

    def __init__(self, target: Optional[list[str]] = None, timeout: int = 15):
        # Default target: debug a fresh cdb.exe child, stopped at its initial
        # breakpoint (good enough for connect / inspect / close scenarios).
        self.target = target if target is not None else ["-o", "cdb.exe"]
        self.timeout = timeout
        self.port = free_port()
        self.process: Optional[subprocess.Popen] = None
        self.output_lines: list[str] = []
        self._reader: Optional[threading.Thread] = None
        self._running = False

    @property
    def connection_string(self) -> str:
        return f"tcp:Port={self.port},Server=127.0.0.1"

    def start(self) -> None:
        cdb_path = find_cdb()
        if not cdb_path:
            raise RuntimeError("Could not find cdb.exe to start a remote server")

        cdb_dir = os.path.dirname(cdb_path)
        self.process = subprocess.Popen(
            [cdb_path, *self.target],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cdb_dir,  # so bare names like "cdb.exe"/"waitfor.exe" resolve
        )

        self._running = True
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

        if not self._wait_for_prompt(self.timeout):
            self.cleanup()
            raise RuntimeError("CDB remote server did not reach its initial prompt")

        # Open the remote server.
        self.write(f".server tcp:port={self.port}")

        # Best-effort wait for the "Server started" confirmation.
        deadline = time.time() + 5
        while time.time() < deadline:
            if any("Server started" in line for line in self.output_lines[-10:]):
                break
            time.sleep(0.1)

    def write(self, text: str) -> None:
        """Write a raw line to the server's stdin (used to resume the target)."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Remote server is not running")
        self.process.stdin.write(text if text.endswith("\n") else text + "\n")
        self.process.stdin.flush()

    def cleanup(self) -> None:
        self._running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write("q\n")
                self.process.stdin.flush()
                self.process.wait(timeout=3)
            except Exception:
                pass
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=1)
        self.process = None

    def __enter__(self) -> "RemoteCdbServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    def _read_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            for line in self.process.stdout:
                self.output_lines.append(line.rstrip())
        except Exception:
            pass

    def _wait_for_prompt(self, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for line in self.output_lines[-10:]:
                if ":000>" in line or "Break instruction exception" in line:
                    return True
            time.sleep(0.1)
        return False
