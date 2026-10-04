"""Opt-in, 45-second native checks using only the bundled dump and localhost.

Run: uv run python src/mcp_windbg/tests/e2e/check_native_protocol.py
Pass --cdb PATH to check a specific SDK CDB instead of the default alias.
No live process, remote machine, kernel target, or symbol server is used.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from mcp_windbg import debug_session
from mcp_windbg.cdb_session import CDBSession, DEFAULT_CDB_PATHS

DUMP = Path(__file__).resolve().parents[1] / "dumps/DemoCrash1.exe.7088.dmp"
REPO = Path(__file__).resolve().parents[4]


def kill_owned(process):
    if process.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True)
        process.wait(timeout=5)


def worker(executable):
    os.environ["_NT_SYMBOL_PATH"] = ""
    os.environ["_NT_ALT_SYMBOL_PATH"] = ""
    debug_session._acp_is_multibyte = lambda: True
    root = REPO / "__tmp/native-protocol-check"
    root.mkdir(parents=True, exist_ok=True)
    original_temp = tempfile.tempdir
    try:
        for name in ("no_spaces", "with spaces"):
            directory = root / name
            directory.mkdir(exist_ok=True)
            tempfile.tempdir = str(directory)
            with CDBSession(dump_path=str(DUMP), cdb_path=executable, symbols_path=str(DUMP.parent), additional_args=["-sins"], timeout=8) as session:
                assert session._log_active
                log_path = Path(session._log_path)
                assert session.send_command(".echo ascii_ok", timeout=3) == ["ascii_ok"]
                assert session.send_command(".echo unicode_\u4e2d\u6587", timeout=3) == ["unicode_\u4e2d\u6587"]
            assert session.process is None and not session.reader_thread.is_alive()
            assert not log_path.exists()
            print(f"PASS: dump, Unicode, path quoting and cleanup ({name})", flush=True)
    finally:
        tempfile.tempdir = original_temp

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = "NATIVE_SERVER_READY"
    ready = threading.Event()
    server = subprocess.Popen([executable, "-server", f"tcp:port={port}", "-sins", "-y", str(DUMP.parent), "-z", str(DUMP), "-c", f".echo {token}"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")

    def drain():
        for line in iter(lambda: server.stdout.readline(64 * 1024), ""):
            if line.strip() == token or line.rstrip().endswith("> " + token):
                ready.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        assert ready.wait(8), "Local dump server did not reach its initial prompt"
        with CDBSession(remote_connection=f"tcp:port={port},server=127.0.0.1", cdb_path=executable, timeout=8) as client:
            output = client.send_command(".echo remote_ok", timeout=3)
            assert any(line.rsplit("> ", 1)[-1] == "remote_ok" for line in output), output
        assert client.process is None and not client.reader_thread.is_alive()
        assert server.poll() is None, "Closing the client unexpectedly terminated the independent server"
        ready.clear()
        server.stdin.write(f".echo {token}\n")
        server.stdin.flush()
        assert ready.wait(3), "Independent server stopped responding after client cleanup"
        print("PASS: real localhost CDB context probe and independent client cleanup", flush=True)
    finally:
        try:
            if server.poll() is None:
                server.stdin.write("q\n")
                server.stdin.flush()
                server.wait(timeout=3)
        finally:
            kill_owned(server)
            reader.join(timeout=2)
            assert not reader.is_alive()
            for pipe in (server.stdin, server.stdout):
                pipe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdb", help="Path to the CDB executable")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    executable = args.cdb or next((path for path in DEFAULT_CDB_PATHS if Path(path).is_file()), None)
    if not executable:
        parser.error("No installed CDB found")
    if args.worker:
        worker(executable)
        return 0
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", "--cdb", executable], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        output, _ = process.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        kill_owned(process)
        raise RuntimeError("Native checks exceeded 45 seconds; their own process tree was killed")
    print(output.decode("utf-8", errors="replace"))
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
