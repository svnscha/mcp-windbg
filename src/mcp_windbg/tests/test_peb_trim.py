"""The remote open's triage trims the environment-variable block `!peb` prints."""

from __future__ import annotations

import pytest
from mcp.types import CallToolRequestParams

import mcp_windbg.server as server_module
from mcp_windbg.server import _trim_peb_environment


# The shape is real cdb output: the struct fields separate name from value
# with `:` and whitespace, the environment variables are indented by 8
# spaces (drive-relative entries start with `=`), and the block runs to the
# end of the command output. The values are made up.
_PEB_OUTPUT = [
    "PEB at 000000e14194e000",
    "    InheritedAddressSpace:    No",
    "    ReadImageFileExecOptions: No",
    "    BeingDebugged:            No",
    "    ImageBaseAddress:         00007ff7f4160000",
    "    NtGlobalFlag:             470",
    "    Ldr                       00007ff819477440",
    "            7ff7f4160000 83d7adcb Feb 04 14:33",
    "    SubSystemData:     0000000000000000",
    "    ProcessHeap:       00000255ab540000",
    "    ProcessParameters: 00000255ab548520",
    "    CurrentDirectory:  'C:\\apps'",
    "    ImageFile:    'C:\\apps\\target.exe'",
    "    Environment:  0000018371a614b0",
    "        =C:=C:\\apps",
    "        =ExitCode=00000000",
    "        ALLUSERSPROFILE=C:\\ProgramData",
    "        API_KEY=hunter2",
    "        DOT-NET.FRAMEWORK=C:\\Windows",
    "        PATH=C:\\Windows\\system32;C:\\Windows",
]


def test_the_block_is_trimmed_with_a_count_and_a_pointer_to_the_full_list():
    text = "\n".join(_trim_peb_environment(_PEB_OUTPUT))
    assert "=C:=C:\\apps" not in text
    assert "=ExitCode=00000000" not in text
    assert "ALLUSERSPROFILE" not in text
    assert "API_KEY=hunter2" not in text
    assert "DOT-NET.FRAMEWORK" not in text
    assert "PATH=" not in text
    # What the triage is for - which process this is and how it started - stays.
    assert "ImageFile:    'C:\\apps\\target.exe'" in text
    # The block's address survives, so memory can still be walked by hand.
    assert "Environment:  0000018371a614b0" in text
    assert "(6 environment variables trimmed; run `!peb` or pass include_environment: true for the full list)" in text


def test_output_without_an_environment_section_is_returned_unchanged():
    lines = [
        "PEB at 000000e14194e000",
        "    BeingDebugged:            No",
        "    ProcessParameters: 00000255ab548520",
    ]
    assert _trim_peb_environment(lines) == lines


def test_an_environment_header_without_variables_is_returned_unchanged():
    lines = ["PEB at 000000e14194e000", "    Environment:  0000018371a614b0"]
    assert _trim_peb_environment(lines) == lines


def test_a_line_that_is_not_a_variable_stops_the_block():
    trimmed = _trim_peb_environment([
        "    Environment:  0000018371a614b0",
        "        ALLUSERSPROFILE=C:\\ProgramData",
        "        PATH=C:\\Windows",
        "quit:",
        "NatVis script unloaded from 'C:\\debuggers\\Visualizers\\windows.natvis'",
    ])
    text = "\n".join(trimmed)
    assert "(2 environment variables trimmed; run `!peb` or pass include_environment: true for the full list)" in text
    assert "quit:" in text
    assert "NatVis script unloaded" in text
    assert "ALLUSERSPROFILE" not in text and "PATH=" not in text


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


@pytest.mark.anyio
async def test_include_environment_keeps_the_block_in_the_open(make_handler):
    handler = make_handler()
    open_result = await handler(
        None,
        CallToolRequestParams(
            name="open_cdb_remote",
            arguments={"connection_string": "tcp:Port=5005,Server=host", "include_environment": True},
        ),
    )
    text = open_result.content[0].text
    assert "API_KEY=hunter2" in text
    assert "environment variables trimmed" not in text
