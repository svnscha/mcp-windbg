"""Restricted command mode (--restrict-commands): what it refuses and what it lets through."""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolRequestParams

from mcp_windbg import server as server_module
from mcp_windbg.command_policy import (
    CommandPolicyError,
    check_command,
    check_local_path,
    check_symbols_path,
)

BLOCKED = [
    ".shell calc",
    ".SHELL -ci \"!peb\" findstr x",
    ".create C:\\evil.exe",
    ".attach 0n1234",
    ".dbgdbg",
    ".server tcp:port=5005",
    ".remote session",
    ".netuse /a \\\\host\\share",
    ".load C:\\evil.dll",
    ".loadby sos clr",
    ".setdll C:\\evil.dll",
    ".scriptload C:\\evil.js",
    ".scriptrun C:\\evil.js",
    ".opendump \\\\attacker\\share\\x.dmp",
    ".ocommand MYCMD:",
    ".writemem C:\\Users\\me\\x.exe 0x1000 L0x100",
    ".dump /ma C:\\out.dmp",
    ".dumpcab -a C:\\out.cab",
    ".logopen C:\\out.txt",
    ".logappend C:\\out.txt",
    ".write_cmd_hist C:\\out.txt",
    ".kdfiles C:\\map.ini",
    ".send_file C:\\x \\\\target\\x",
    ".foreach (t {lm1m}) {.echo t}",
    ".sympath \\\\attacker\\share",
    ".sympath+ srv*https://attacker.example/sym",
    ".symfix \\\\attacker\\cache",
    ".srcpath \\\\attacker\\src",
    ".extpath C:\\evil",
    ".cordll -lp C:\\evil",
    "$<C:\\script.txt",
    "$$><C:\\script.txt",
    "$$>a<C:\\script.txt arg",
    "!C:\\evil\\ext.cmd",
    "!\\\\attacker\\share\\ext.cmd",
    "as x .she",
    "aS x .she",
    "r $.u0 = .she",
    "$u0ll calc",
    "dx Debugger.Utility.Control.ExecuteCommand(\".sh\" + \"ell calc\")",
    "dx @$debuggerRootNamespace.Debugger.Utility.FileSystem.CreateFile(\"C:\\\\x\")",
    # Nested in another command: still refused.
    "kb; .shell calc",
    "kb\n.shell calc",
    ".if (1) { .shell calc }",
    "bp kernel32!CreateFileW \".shell calc; g\"",
    "~*e .shell calc",
]

ALLOWED = [
    "kb",
    "k 20",
    "lm",
    "lmvm ntdll",
    "!analyze -v",
    "!peb",
    "!process 0 0",
    "!heap -p -a 0x1234",
    "dt nt!_EPROCESS",
    "dt ntdll!_PEB @$peb",
    "u kernel32!CreateFileW",
    ".lastevent",
    ".reload /f",
    ".ecxr",
    ".frame 2",
    ".sympath",
    ".srcpath",
    ".exr -1",
    ".cordll -ve -u -l",
    ".servers",
    ".echo hello",
    "~*k",
    "dx @$curprocess.Threads",
    "bp kernel32!CreateFileW",
    "g",
    "r rax",
    "r $t0 = 5",
    "da poi(@rsp)",
    "!sym noisy",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocked_commands_are_refused(command):
    with pytest.raises(CommandPolicyError, match="--restrict-commands"):
        check_command(command)


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_analysis_commands_are_allowed(command):
    check_command(command)


@pytest.mark.parametrize("path", [
    r"\\attacker\share\x.dmp",
    "//attacker/share/x.dmp",
    r"\\?\UNC\attacker\share\x.dmp",
    "https://attacker.example/x.dmp",
    "file://attacker/share/x.dmp",
    r'"\\attacker\share\x.dmp"',
])
def test_network_paths_are_refused(path):
    with pytest.raises(CommandPolicyError, match="network paths"):
        check_local_path(path, "dump_path")


@pytest.mark.parametrize("path", [
    r"C:\dumps\app.dmp",
    r"\\?\C:\very\long\path\app.dmp",
    r"\\.\C:\dumps\app.dmp",
    r"dumps\app.dmp",
    None,
    "",
])
def test_local_paths_are_allowed(path):
    check_local_path(path, "dump_path")


@pytest.mark.parametrize("symbols", [
    r"\\attacker\symbols",
    r"C:\sym;\\attacker\symbols",
    r"srv*C:\cache*https://attacker.example/symbols",
    r"cache*\\attacker\cache",
])
def test_network_symbol_paths_are_refused(symbols):
    with pytest.raises(CommandPolicyError, match="symbols_path"):
        check_symbols_path(symbols)


@pytest.mark.parametrize("symbols", [None, "", r"C:\build\out", r"C:\a;D:\b", r"cache*C:\cache"])
def test_local_symbol_paths_are_allowed(symbols):
    check_symbols_path(symbols)


# -- Server integration -------------------------------------------------------


class _RecordingSession:
    is_live_session = False

    def __init__(self, **kwargs):
        self.commands: list[str] = []
        _RecordingSession.last = self

    def send_command(self, command, timeout=None):
        self.commands.append(command)
        return [f"OUT:{command}"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def make_handler(monkeypatch):
    monkeypatch.setattr(server_module, "_sessions", {})
    monkeypatch.setattr(server_module, "CDBSession", _RecordingSession)
    monkeypatch.setattr(server_module, "KDSession", _RecordingSession)

    def _factory(restrict_commands, init_commands=None):
        captured = {}
        real_server = server_module.Server

        def record_handlers(*args, **kwargs):
            captured.update(kwargs)
            return real_server(*args, **kwargs)

        monkeypatch.setattr(server_module, "Server", record_handlers)
        server_module._create_server(
            init_commands=init_commands, restrict_commands=restrict_commands
        )
        return captured["on_call_tool"]

    return _factory


async def _call(handler, name, arguments):
    return await handler(None, CallToolRequestParams(name=name, arguments=arguments))


async def _open_dump(handler):
    result = await _call(handler, "open_cdb_dump", {"dump_path": r"C:\dumps\app.dmp"})
    return result.content[0].text.split("session_id: ", 1)[1].split()[0]


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["run_cdb_command", "run_kd_command"])
async def test_restricted_server_refuses_blocked_command(make_handler, tool):
    handler = make_handler(restrict_commands=True)
    with pytest.raises(MCPError, match="--restrict-commands"):
        # Refused before the session is looked up, so no session is needed.
        await _call(handler, tool, {"session_id": "cdb-unknown", "command": ".shell calc"})


@pytest.mark.anyio
async def test_restricted_server_runs_ordinary_commands(make_handler):
    handler = make_handler(restrict_commands=True)
    session_id = await _open_dump(handler)
    result = await _call(handler, "run_cdb_command", {"session_id": session_id, "command": "kb"})
    assert "OUT:kb" in result.content[0].text


@pytest.mark.anyio
async def test_unrestricted_server_passes_any_command(make_handler):
    handler = make_handler(restrict_commands=False)
    session_id = await _open_dump(handler)
    await _call(handler, "run_cdb_command", {"session_id": session_id, "command": ".shell -? x"})
    assert _RecordingSession.last.commands[-1] == ".shell -? x"


@pytest.mark.anyio
@pytest.mark.parametrize("tool, arguments", [
    ("open_cdb_dump", {"dump_path": r"\\attacker\share\x.dmp"}),
    ("open_kd_dump", {"dump_path": r"\\attacker\share\MEMORY.DMP"}),
    ("list_dumps", {"directory_path": r"\\attacker\share"}),
    ("open_cdb_dump", {"dump_path": r"C:\dumps\app.dmp", "symbols_path": r"\\attacker\sym"}),
    ("open_cdb_remote", {"connection_string": "tcp:Port=5005,Server=host",
                         "symbols_path": "srv*https://attacker.example"}),
    ("open_kd_session", {"connection_string": "net:port=50000,key=1.2.3.4",
                         "symbols_path": r"\\attacker\sym"}),
])
async def test_restricted_server_refuses_network_paths(make_handler, tool, arguments):
    handler = make_handler(restrict_commands=True)
    with pytest.raises(MCPError, match="--restrict-commands"):
        await _call(handler, tool, arguments)


@pytest.mark.anyio
async def test_init_commands_are_operator_config_and_not_restricted(make_handler):
    handler = make_handler(restrict_commands=True, init_commands=[r".load C:\ext\mine.dll"])
    await _open_dump(handler)
    assert _RecordingSession.last.commands[0] == r".load C:\ext\mine.dll"


@pytest.mark.anyio
async def test_session_ids_carry_the_full_random_value(make_handler):
    handler = make_handler(restrict_commands=False)
    session_id = await _open_dump(handler)
    assert len(session_id) == len("cdb-") + 32
