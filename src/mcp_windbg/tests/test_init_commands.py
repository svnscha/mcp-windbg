"""Init commands (--init-command / MCP_WINDBG_INIT_COMMANDS) run on every new session."""

from __future__ import annotations

import pytest
from mcp.types import CallToolRequestParams

import mcp_windbg
from mcp_windbg import server as server_module


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

    def _factory(init_commands):
        captured = {}
        real_server = server_module.Server

        def record_handlers(*args, **kwargs):
            captured.update(kwargs)
            return real_server(*args, **kwargs)

        monkeypatch.setattr(server_module, "Server", record_handlers)
        server_module._create_server(init_commands=init_commands)
        return captured["on_call_tool"]

    return _factory


OPEN_CALLS = [
    ("open_cdb_dump", {"dump_path": r"C:\dumps\app.dmp"}, ".lastevent"),
    ("open_cdb_remote", {"connection_string": "tcp:Port=5005,Server=host"}, "!peb"),
    ("open_kd_session", {"connection_string": "net:port=50000,key=1.2.3.4"}, "vertarget"),
]


@pytest.mark.anyio
@pytest.mark.parametrize("tool, arguments, first_triage", OPEN_CALLS)
async def test_init_commands_run_before_triage_on_every_open(make_handler, tool, arguments, first_triage):
    handler = make_handler([r".load D:\ext\extension.dll", "!winver"])
    result = await handler(None, CallToolRequestParams(name=tool, arguments=arguments))

    commands = _RecordingSession.last.commands
    # The extension must be loaded before triage so triage can use it.
    assert commands[:3] == [r".load D:\ext\extension.dll", "!winver", first_triage]
    text = result.content[0].text
    assert "### Initialization" in text
    assert "> !winver\nOUT:!winver" in text
    assert text.index("### Initialization") < text.index("OUT:" + first_triage)


@pytest.mark.anyio
async def test_no_init_commands_means_no_initialization_section(make_handler):
    handler = make_handler(None)
    result = await handler(
        None, CallToolRequestParams(name="open_cdb_dump", arguments={"dump_path": r"C:\dumps\app.dmp"})
    )
    assert _RecordingSession.last.commands[0] == ".lastevent"
    assert "### Initialization" not in result.content[0].text


def test_init_commands_come_from_the_environment_one_per_line():
    environ = {"MCP_WINDBG_INIT_COMMANDS": ".load D:\\ext\\extension.dll\n\n  !winver  \n"}
    assert mcp_windbg._resolve_init_commands(None, environ) == [".load D:\\ext\\extension.dll", "!winver"]


def test_init_command_flags_take_precedence_over_the_environment():
    environ = {"MCP_WINDBG_INIT_COMMANDS": "!from_env"}
    assert mcp_windbg._resolve_init_commands(["!from_cli"], environ) == ["!from_cli"]


def test_no_init_commands_configured():
    assert mcp_windbg._resolve_init_commands(None, {}) == []
