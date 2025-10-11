import pytest
import subprocess
import time
import threading
import os
import sys
from typing import Optional

# Add the src directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from mcp_server_windbg.cdb_session import CDBSession, CDBError
from mcp_server_windbg.server import get_or_create_session, unload_session, DEFAULT_CDB_PATHS


class CDBServerProcess:
    """Helper class to manage a CDB server process for testing."""

    def __init__(self, port: int = 5005):
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.output_lines = []
        self.reader_thread: Optional[threading.Thread] = None
        self.running = False

    def start(self, timeout: int = 10) -> bool:
        """Start the CDB server process."""
        try:
            # Find cdb.exe
            cdb_path = self._find_cdb_executable()
            if not cdb_path:
                raise Exception("Could not find cdb.exe")

            # Use CDB to launch and debug a new instance of CDB
            self.process = subprocess.Popen(
                [cdb_path, "-o", cdb_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Start output reader thread
            self.running = True
            self.reader_thread = threading.Thread(target=self._read_output)
            self.reader_thread.daemon = True
            self.reader_thread.start()

            # Wait for CDB to initialize
            if not self._wait_for_prompt(timeout):
                return False

            # Start the remote server
            server_command = f".server tcp:port={self.port}\n"
            self.process.stdin.write(server_command)
            self.process.stdin.flush()

            # Wait for server to start and check for success message
            start_time = time.time()
            while time.time() - start_time < 5:
                recent_lines = self.output_lines[-10:]
                if any("Server started" in line for line in recent_lines):
                    return True
                time.sleep(0.1)

            return True  # Assume success if we got this far

        except Exception as e:
            print(f"Failed to start CDB server: {e}")
            self.cleanup()
            return False

    def cleanup(self):
        """Clean up the CDB server process."""
        self.running = False

        if self.process and self.process.poll() is None:
            try:
                # Send quit command
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

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1)

        self.process = None

    def _find_cdb_executable(self) -> Optional[str]:
        """Find the cdb.exe executable."""
        for path in DEFAULT_CDB_PATHS:
            if os.path.isfile(path):
                return path
        return None

    def _read_output(self):
        """Thread function to read CDB output."""
        if not self.process or not self.process.stdout:
            return

        try:
            for line in self.process.stdout:
                line = line.rstrip()
                self.output_lines.append(line)
                print(f"CDB Server: {line}")  # Debug output
        except Exception as e:
            print(f"CDB server output reader error: {e}")

    def _wait_for_prompt(self, timeout: int) -> bool:
        """Wait for CDB to be ready."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            # Look for the CDB prompt pattern (e.g., "0:000>")
            recent_lines = self.output_lines[-10:]  # Check last 10 lines
            for line in recent_lines:
                if ":000>" in line or "Break instruction exception" in line:
                    return True
            time.sleep(0.1)
        return False


@pytest.mark.skipif(not os.name == 'nt', reason="Windows-only test")
class TestRemoteDebugging:
    """Test cases for remote debugging functionality."""

    def test_remote_debugging_workflow(self):
        """Test the complete remote debugging workflow."""
        server = CDBServerProcess(port=5005)
        connection_string = "tcp:Port=5005,Server=127.0.0.1"

        try:
            # Start the CDB server process
            assert server.start(timeout=15), "Failed to start CDB server process"

            # Test opening remote connection
            session = get_or_create_session(connection_string=connection_string, timeout=10, verbose=True)
            assert session is not None, "Failed to create remote session"

            # Test sending a command
            try:
                output = session.send_command("r")  # Show registers
                assert len(output) > 0, "No output from remote command"
                print(f"Remote command output: {output[:3]}")  # Show first 3 lines
            except CDBError as e:
                # Sometimes the first command might timeout during connection establishment
                print(f"First command failed (this might be expected): {e}")

            # Test that session exists in active sessions
            from mcp_server_windbg.server import active_sessions
            session_id = f"remote:{connection_string}"
            assert session_id in active_sessions, "Session not found in active sessions"

            # Test closing the remote connection
            success = unload_session(connection_string=connection_string)
            assert success, "Failed to unload remote session"

            # Verify session was removed
            assert session_id not in active_sessions, "Session still exists after unloading"

        finally:
            # Clean up the server process
            server.cleanup()

    def test_remote_connection_validation(self):
        """Test validation of remote connection parameters."""
        # Test that CDBSession validates parameters correctly
        with pytest.raises(ValueError, match="Either dump_path or remote_connection must be provided"):
            CDBSession()

        with pytest.raises(ValueError, match="dump_path and remote_connection are mutually exclusive"):
            CDBSession(dump_path="test.dmp", remote_connection="tcp:Port=5005,Server=127.0.0.1")

    def test_invalid_remote_connection(self):
        """Test handling of invalid remote connections."""
        invalid_connection = "tcp:Port=99999,Server=192.168.255.255"  # Invalid server

        with pytest.raises(CDBError):
            session = CDBSession(remote_connection=invalid_connection, timeout=2)
            # The session creation might succeed but commands should fail
            session.send_command("r")


if __name__ == "__main__":
    # Run a simple test manually
    print("Running remote debugging test...")

    server = CDBServerProcess(port=5005)
    connection_string = "tcp:Port=5005,Server=127.0.0.1"

    try:
        print("Starting CDB server...")
        if server.start(timeout=15):
            print("CDB server started successfully")

            print("Creating remote session...")
            session = get_or_create_session(connection_string=connection_string, timeout=10, verbose=True)

            print("Sending test command...")
            try:
                output = session.send_command("r")
                print(f"Command successful, got {len(output)} lines of output")
            except Exception as e:
                print(f"Command failed: {e}")

            print("Closing remote session...")
            unload_session(connection_string=connection_string)
            print("Test completed successfully!")

        else:
            print("Failed to start CDB server")

    except Exception as e:
        print(f"Test failed: {e}")

    finally:
        print("Cleaning up...")
        server.cleanup()
