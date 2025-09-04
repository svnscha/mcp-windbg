import os
import json
import time
import threading
import glob
from typing import List, Optional, Dict, Any
from pathlib import Path
import tempfile

class WinDbgError(Exception):
    """Custom exception for WinDbg-related errors"""
    pass

class WinDbgSession:
    def __init__(
        self,
        dump_path: Optional[str] = None,
        timeout: int = 30,
        verbose: bool = False,
        temp_dir: Optional[str] = None
    ):
        """
        Initialize a new WinDbg session that communicates with the JavaScript plugin.

        Args:
            dump_path: Path to the crash dump file (optional for live debugging)
            timeout: Timeout in seconds for waiting for WinDbg responses
            verbose: Whether to print additional debug information
            temp_dir: Custom temporary directory for communication files

        Raises:
            WinDbgError: If communication cannot be established
        """
        self.dump_path = dump_path
        self.timeout = timeout
        self.verbose = verbose
        # Use C:\Temp to match the JavaScript plugin
        self.temp_dir = temp_dir or "C:\\Temp"

        # Communication state
        self.session_id = None
        self.comm_file = None
        self.result_file = None
        self.shutdown_file = None
        self.is_connected = False
        self.last_command_id = None

        # Threading
        self.lock = threading.Lock()
        self.monitor_thread = None
        self.stop_monitoring = False

        # Ensure temp directory exists
        os.makedirs(self.temp_dir, exist_ok=True)

        # Try to find an existing WinDbg session
        self._find_existing_session()

    def _find_existing_session(self):
        """Find an existing WinDbg MCP session"""
        try:
            # Look for communication files in temp directory
            pattern = os.path.join(self.temp_dir, "windbg_mcp_*.json")
            comm_files = glob.glob(pattern)

            if comm_files:
                # Filter out result and shutdown files
                init_files = [f for f in comm_files if not f.endswith('_result.json') and not f.endswith('_shutdown.json')]

                if init_files:
                    # Use the most recent session
                    init_files.sort(key=os.path.getmtime, reverse=True)
                    self.comm_file = init_files[0]

                    # Extract session ID from filename
                    filename = os.path.basename(self.comm_file)
                    self.session_id = filename.replace("windbg_mcp_", "").replace(".json", "")

                    # Set up related files
                    self.result_file = self.comm_file.replace('.json', '_result.json')
                    self.shutdown_file = self.comm_file.replace('.json', '_shutdown.json')

                    # Check if session is still active
                    if self._check_session_active():
                        self.is_connected = True
                        if self.verbose:
                            print(f"Connected to existing WinDbg session: {self.session_id}")
                    else:
                        if self.verbose:
                            print(f"Found inactive session: {self.session_id}")

        except Exception as e:
            if self.verbose:
                print(f"Error finding existing session: {e}")

    def _check_session_active(self) -> bool:
        """Check if the WinDbg session is still active"""
        try:
            if not self.comm_file or not os.path.exists(self.comm_file):
                return False

            # Read the communication file
            with open(self.comm_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if not content:
                if self.verbose:
                    print("Communication file is empty")
                return False

            # Try to parse JSON (handle double-encoded JSON from JS plugin)
            try:
                data = json.loads(content)
                # If the result is a string, it might be double-encoded JSON
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        if self.verbose:
                            print(f"Failed to parse double-encoded JSON: {data[:200]}...")
                        return False
            except json.JSONDecodeError as je:
                if self.verbose:
                    print(f"JSON decode error: {je}")
                    print(f"File content: {content[:200]}...")
                return False

            # Ensure data is a dictionary
            if not isinstance(data, dict):
                if self.verbose:
                    print(f"Expected dict, got {type(data)}: {data}")
                return False

            # Check if it's a recent init message
            if data.get('type') == 'init' and data.get('status') == 'ready':
                timestamp = data.get('timestamp', 0)
                current_time = time.time() * 1000  # Convert to milliseconds

                # Consider session active if init was within last 5 minutes
                return (current_time - timestamp) < (5 * 60 * 1000)

        except Exception as e:
            if self.verbose:
                print(f"Error checking session status: {e}")

        return False

    def wait_for_connection(self, timeout: Optional[int] = None) -> bool:
        """
        Wait for WinDbg to establish connection

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if connection established, False if timeout
        """
        wait_timeout = timeout or self.timeout
        start_time = time.time()

        while time.time() - start_time < wait_timeout:
            self._find_existing_session()
            if self.is_connected:
                return True
            time.sleep(0.5)

        return False

    def send_command(self, command: str, timeout: Optional[int] = None) -> List[str]:
        """
        Send a command to WinDbg and return the output

        Args:
            command: The WinDbg command to send
            timeout: Custom timeout for this command

        Returns:
            List of output lines from WinDbg

        Raises:
            WinDbgError: If the command fails or times out
        """
        if not self.is_connected or not self.comm_file:
            raise WinDbgError("Not connected to WinDbg. Make sure the JavaScript plugin is loaded and initialized with !mcp_init.")

        cmd_timeout = timeout or self.timeout

        with self.lock:
            # Generate command ID
            command_id = f"cmd_{int(time.time() * 1000)}_{os.getpid()}"
            self.last_command_id = command_id

            # Create command request
            command_data = {
                "type": "command",
                "id": command_id,
                "command": command,
                "timestamp": int(time.time() * 1000),
                "sessionId": self.session_id  # Match JS plugin field name
            }

            # Write command to communication file
            try:
                with open(self.comm_file, 'w') as f:
                    json.dump(command_data, f, indent=2)

                if self.verbose:
                    print(f"Sent command: {command}")

            except Exception as e:
                raise WinDbgError(f"Failed to send command: {e}")

            # Wait for result
            return self._wait_for_result(command_id, cmd_timeout)

    def _wait_for_result(self, command_id: str, timeout: int) -> List[str]:
        """Wait for command result from WinDbg"""
        if not self.result_file:
            raise WinDbgError("Result file not configured")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if os.path.exists(self.result_file):
                    with open(self.result_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()

                    if not content:
                        time.sleep(0.1)
                        continue

                    try:
                        result_data = json.loads(content)
                    except json.JSONDecodeError:
                        # Result file might be partially written, wait a bit more
                        time.sleep(0.1)
                        continue

                    if not isinstance(result_data, dict):
                        if self.verbose:
                            print(f"Expected dict in result, got {type(result_data)}")
                        time.sleep(0.1)
                        continue

                    # Check if this is the result we're waiting for
                    if result_data.get('id') == command_id:
                        # Clean up result file
                        try:
                            os.remove(self.result_file)
                        except:
                            pass

                        if result_data.get('success', False):
                            output = result_data.get('output', [])
                            if self.verbose:
                                print(f"Command completed in {result_data.get('executionTime', 0)}ms")
                            return output
                        else:
                            error_msg = result_data.get('error', 'Unknown error')
                            raise WinDbgError(f"Command failed: {error_msg}")

            except Exception as e:
                if self.verbose:
                    print(f"Error reading result: {e}")

            time.sleep(0.1)

        raise WinDbgError(f"Command timed out after {timeout} seconds: {command_id}")

    def get_session_info(self) -> Dict[str, Any]:
        """Get information about the current session"""
        return {
            "session_id": self.session_id,
            "is_connected": self.is_connected,
            "dump_path": self.dump_path,
            "comm_file": self.comm_file,
            "result_file": self.result_file,
            "timeout": self.timeout,
            "temp_dir": self.temp_dir
        }

    def execute_analysis_commands(self) -> Dict[str, List[str]]:
        """
        Execute common analysis commands and return the results.

        Returns a dictionary with the results of various analysis commands.
        """
        results = {}

        try:
            # Basic information
            results["version"] = self.send_command("version")
            results["target"] = self.send_command("vertarget")

            # Last event and analysis
            results["last_event"] = self.send_command("!analyze -v")

            # Stack trace
            results["stack_trace"] = self.send_command("k")

            # Registers
            results["registers"] = self.send_command("r")

            # Modules
            results["modules"] = self.send_command("lm")

            # Threads
            results["threads"] = self.send_command("~")

            # Exception information
            results["exception"] = self.send_command(".exr -1")

        except WinDbgError as e:
            if self.verbose:
                print(f"Error during analysis: {e}")
            results["error"] = str(e)

        return results

    def get_stack_trace(self) -> List[str]:
        """Get the current stack trace"""
        return self.send_command("k")

    def get_registers(self) -> List[str]:
        """Get current register values"""
        return self.send_command("r")

    def get_modules(self) -> List[str]:
        """Get loaded modules"""
        return self.send_command("lm")

    def get_threads(self) -> List[str]:
        """Get thread information"""
        return self.send_command("~")

    def analyze_crash(self) -> List[str]:
        """Run crash analysis"""
        return self.send_command("!analyze -v")

    def get_exception_info(self) -> List[str]:
        """Get exception information"""
        return self.send_command(".exr -1")

    def close(self):
        """Close the WinDbg session"""
        try:
            if self.is_connected and self.shutdown_file:
                # Signal shutdown to the JavaScript plugin
                shutdown_data = {
                    "type": "shutdown",
                    "sessionId": self.session_id,
                    "timestamp": int(time.time() * 1000)
                }

                with open(self.shutdown_file, 'w') as f:
                    json.dump(shutdown_data, f, indent=2)

            # Reset state
            self.is_connected = False
            self.session_id = None
            self.comm_file = None
            self.result_file = None
            self.shutdown_file = None

            if self.verbose:
                print("WinDbg session closed")

        except Exception as e:
            if self.verbose:
                print(f"Error closing session: {e}")

    def shutdown(self):
        """Alias for close() method for backward compatibility"""
        self.close()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

    def list_available_sessions(self) -> List[Dict[str, Any]]:
        """List all available WinDbg MCP sessions"""
        sessions = []

        try:
            pattern = os.path.join(self.temp_dir, "windbg_mcp_*.json")
            comm_files = glob.glob(pattern)

            # Filter out result and shutdown files
            init_files = [f for f in comm_files if not f.endswith('_result.json') and not f.endswith('_shutdown.json')]

            for comm_file in init_files:
                try:
                    with open(comm_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()

                    if not content:
                        continue

                    try:
                        data = json.loads(content)
                        # Handle double-encoded JSON from JS plugin
                        if isinstance(data, str):
                            try:
                                data = json.loads(data)
                            except json.JSONDecodeError:
                                if self.verbose:
                                    print(f"Failed to parse double-encoded JSON in {comm_file}")
                                continue
                    except json.JSONDecodeError:
                        if self.verbose:
                            print(f"Invalid JSON in {comm_file}")
                        continue

                    if not isinstance(data, dict):
                        if self.verbose:
                            print(f"Expected dict in {comm_file}, got {type(data)}")
                        continue

                    if data.get('type') == 'init':
                        filename = os.path.basename(comm_file)
                        session_id = filename.replace("windbg_mcp_", "").replace(".json", "")

                        sessions.append({
                            "session_id": session_id,
                            "comm_file": comm_file,
                            "timestamp": data.get('timestamp', 0),
                            "status": data.get('status', 'unknown'),
                            "active": self._is_session_active(comm_file)
                        })

                except Exception as e:
                    if self.verbose:
                        print(f"Error reading session file {comm_file}: {e}")

        except Exception as e:
            if self.verbose:
                print(f"Error listing sessions: {e}")

        return sorted(sessions, key=lambda x: x['timestamp'], reverse=True)

    def _is_session_active(self, comm_file: str) -> bool:
        """Check if a specific session is active"""
        try:
            with open(comm_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if not content:
                return False

            try:
                data = json.loads(content)
                # Handle double-encoded JSON from JS plugin
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        return False
            except json.JSONDecodeError:
                return False

            if not isinstance(data, dict):
                return False

            if data.get('type') == 'init' and data.get('status') == 'ready':
                timestamp = data.get('timestamp', 0)
                current_time = time.time() * 1000
                return (current_time - timestamp) < (5 * 60 * 1000)

        except Exception:
            pass

        return False

    def connect_to_session(self, session_id: str) -> bool:
        """Connect to a specific session by ID"""
        try:
            comm_file = os.path.join(self.temp_dir, f"windbg_mcp_{session_id}.json")

            if os.path.exists(comm_file):
                self.session_id = session_id
                self.comm_file = comm_file
                self.result_file = comm_file.replace('.json', '_result.json')
                self.shutdown_file = comm_file.replace('.json', '_shutdown.json')

                if self._check_session_active():
                    self.is_connected = True
                    if self.verbose:
                        print(f"Connected to session: {session_id}")
                    return True

        except Exception as e:
            if self.verbose:
                print(f"Error connecting to session {session_id}: {e}")

        return False
