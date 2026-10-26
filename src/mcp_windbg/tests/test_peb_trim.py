"""The remote open's triage trims the environment-variable block `!peb` prints."""

from __future__ import annotations

import pytest
from mcp.types import CallToolRequestParams

import mcp_windbg.server as server_module
from mcp_windbg.server import _trim_peb_environment


_PEB_OUTPUT = [
    "PEB at 00000000004a2000",
    "    BeingDebugged:          No",
    "",
    "     ImageFile:    'C:\\apps\\target.exe'",
    "     CommandLine:  'target.exe --flag=1'",
    "     Environment: 00000000004a2e10",
    "ALLUSERSPROFILE=C:\\ProgramData",
    "API_KEY=hunter2",
    "PATH=C:\\Windows\\system32;C:\\Windows",
]


def test_the_block_is_trimmed_with_a_count_and_a_pointer_to_the_full_list():
    text = "\n".join(_trim_peb_environment(_PEB_OUTPUT))
    assert "ALLUSERSPROFILE" not in text
    assert "API_KEY=hunter2" not in text
    # What the triage is for - which process this is and how it started - stays.
    assert "ImageFile:    'C:\\apps\\target.exe'" in text
    assert "CommandLine:  'target.exe --flag=1'" in text
    # The block's address survives, so memory can still be walked by hand.
    assert "Environment: 00000000004a2e10" in text
    assert "(3 environment variables trimmed; run `!peb` for the full list)" in text


def test_output_without_an_environment_section_is_returned_unchanged():
    lines = ["PEB at 00000000004a2000", "    BeingDebugged:          No"]
    assert _trim_peb_environment(lines) == lines


def test_an_environment_header_without_variables_is_returned_unchanged():
    lines = ["PEB at 00000000004a2000", "     Environment: 00000000004a2e10"]
    assert _trim_peb_environment(lines) == lines


def test_a_line_that_is_not_a_variable_stops_the_block():
    trimmed = _trim_peb_environment([
        "     Environment: 00000000004a2e10",
        "ONE=1",
        "TWO=2",
        "some trailing output",
        "THREE=3",
    ])
    text = "\n".join(trimmed)
    assert "(2 environment variables trimmed; run `!peb` for the full list)" in text
    assert "some trailing output" in text
    assert "THREE=3" in text
    assert "ONE=1" not in text and "TWO=2" not in text


class _FixedOutputSession:
    is_live_session = False

    def __init__(self, **kwargs):
        _FixedOutputSession.last = self

    def send_command(self, command, timeout=None):
        # Only `!peb` returns the PEB; the other triage commands get their own
        # small output, so a leak cannot hide in another section.
        if command == "!peb":
            return list(_PEB_OUTPUT)
        return [f"OUT:{command}"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def make_handler(monkeypatch):
    monkeypatch.setattr(server_module, "_sessions", {})
    monkeypatch.setattr(server_module, "CDBSession", _FixedOutputSession)
    monkeypatch.setattr(server_module, "KDSession", _FixedOutputSession)

    def _factory():
        captured = {}
        real_server = server_module.Server

        def record_handlers(*args, **kwargs):
            captured.update(kwargs)
            return real_server(*args, **kwargs)

        monkeypatch.setattr(server_module, "Server", record_handlers)
        server_module._create_server()
        return captured["on_call_tool"]

    return _factory


@pytest.mark.anyio
async def test_the_triage_trims_the_block_but_an_explicit_peb_is_full(make_handler):
    handler = make_handler()
    open_result = await handler(
        None,
        CallToolRequestParams(name="open_cdb_remote", arguments={"connection_string": "tcp:Port=5005,Server=host"}),
    )
    text = open_result.content[0].text
    assert "API_KEY=hunter2" not in text
    assert "environment variables trimmed" in text

    session_id = next(iter(server_module._sessions))
    run_result = await handler(
        None,
        CallToolRequestParams(name="run_cdb_command", arguments={"session_id": session_id, "command": "!peb"}),
    )
    assert "API_KEY=hunter2" in run_result.content[0].text
