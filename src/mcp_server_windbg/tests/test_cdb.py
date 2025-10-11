import os
import pytest

from mcp_server_windbg.cdb_session import CDBSession, CDBError, DEFAULT_CDB_PATHS

# Path to the test dump file
TEST_DUMP_PATH = os.path.join(os.path.dirname(__file__), 'dumps', 'DemoCrash1.exe.7088.dmp')

def setup_cdb_session():
    """Helper function to create a CDB session"""
    if not os.path.exists(TEST_DUMP_PATH):
        pytest.skip("Test dump file not found")

    if not any(os.path.exists(path) for path in DEFAULT_CDB_PATHS):
        pytest.skip("CDB executable not found")

    return CDBSession(
        dump_path=TEST_DUMP_PATH,
        timeout=20,
        verbose=True
    )

def test_basic_cdb_command():
    """Test basic CDB command execution"""
    session = setup_cdb_session()
    try:
        output = session.send_command("version")
        assert len(output) > 0
        assert any("Microsoft (R) Windows Debugger" in line for line in output)
    finally:
        session.shutdown()

def test_command_sequence():
    """Test multiple commands in sequence"""
    session = setup_cdb_session()
    try:
        # Basic command sequence
        commands = ["version", ".sympath", "!analyze -v", "lm", "~"]
        results = []

        for cmd in commands:
            output = session.send_command(cmd)
            results.append((cmd, output))
            assert len(output) > 0

        # Check expected output patterns
        assert any("Microsoft (R) Windows Debugger" in line for line in results[0][1])
        assert any("Symbol search path is:" in line for line in results[1][1])
        assert any("start" in line.lower() for line in results[3][1])
    finally:
        session.shutdown()

def test_module_inspection():
    """Test module inspection capabilities"""
    session = setup_cdb_session()
    try:
        # Get module list
        modules_output = session.send_command("lm")

        # Find a common Windows module
        target_modules = ['ntdll', 'kernel32']
        module_name = None

        for target in target_modules:
            for line in modules_output:
                if target in line.lower():
                    parts = line.split()
                    for part in parts:
                        if target in part.lower():
                            module_name = part
                            break
                    if module_name:
                        break
            if module_name:
                break

        assert module_name is not None

        # Get module details
        module_info = session.send_command(f"lmv m {module_name}")
        assert len(module_info) > 0
        assert any(module_name.lower() in line.lower() for line in module_info)

        # Get stack info
        stack_info = session.send_command("k 5")
        assert len(stack_info) > 0
    finally:
        session.shutdown()

def test_thread_context():
    """Test thread context operations"""
    session = setup_cdb_session()
    try:
        # Get thread list
        thread_list = session.send_command("~")

        # Select first thread
        thread_id = "0"
        for line in thread_list:
            if line.strip().startswith("#"):
                parts = line.split()
                if len(parts) > 1:
                    thread_id = parts[1].strip(":")
                    break

        # Switch to thread and check registers
        session.send_command(f"~{thread_id}s")
        registers = session.send_command("r")
        assert len(registers) > 0
        assert any("eax" in line.lower() or "rax" in line.lower() for line in registers)

        # Check stack trace
        stack = session.send_command("k")
        assert len(stack) > 0
    finally:
        session.shutdown()

if __name__ == "__main__":
    pytest.main(["-v", __file__])
