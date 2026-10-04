"""Failed opens must not leave debugger sessions the caller cannot address."""

from __future__ import annotations

import pytest
from mcp.types import CallToolRequestParams

from mcp_windbg import server as server_module
from mcp_windbg.debug_session import DebuggerError


OPEN_CALLS = [
    ("open_cdb_dump", {"dump_path": "app.dmp"}, ".lastevent", "!analyze -v", "cdb"),
    ("open_cdb_remote", {"connection_string": "test remote"}, "!peb", "r", "cdb"),
    ("open_kd_session", {"connection_string": "test kernel"}, "vertarget", "r", "kd"),
    ("open_kd_dump", {"dump_path": "MEMORY.DMP"}, "vertarget", "!analyze -v", "kd"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def open_handler(monkeypatch):
    monkeypatch.setattr(server_module, "_sessions", {})
    created = []

    class Session:
        failure = None
        shutdown_failure = False

        def __init__(self, **kwargs):
            self.commands = []
            self.shutdown_calls = 0
            created.append(self)

        def send_command(self, command, timeout):
            self.commands.append(command)
            if command == self.failure:
                raise DebuggerError(f"triage failed: {command}")
            return [f"output: {command}"]

        def shutdown(self):
            self.shutdown_calls += 1
            if self.shutdown_failure:
                raise RuntimeError("cleanup failure")

    monkeypatch.setattr(server_module, "CDBSession", Session)
    monkeypatch.setattr(server_module, "KDSession", Session)
    captured = {}
    real_server = server_module.Server

    def record_handlers(*args, **kwargs):
        captured.update(kwargs)
        return real_server(*args, **kwargs)

    monkeypatch.setattr(server_module, "Server", record_handlers)

    def make_handler(content_filter=None):
        server_module._create_server(init_commands=["!init"], content_filter=content_filter)
        return captured["on_call_tool"]

    return make_handler, Session, created


@pytest.mark.anyio
@pytest.mark.parametrize("tool,arguments,first,second,kind", OPEN_CALLS)
@pytest.mark.parametrize("stage", ["init", "first", "second", "optional"])
async def test_failed_open_closes_only_its_session(open_handler, tool, arguments, first, second, kind, stage):
    make_handler, session_type, created = open_handler
    handler = make_handler()
    failure = {"init": "!init", "first": first, "second": second, "optional": "kb"}[stage]
    session_type.failure = failure
    unrelated = {"session": object(), "kind": kind, "label": "existing session"}
    server_module._sessions["existing"] = unrelated
    params = CallToolRequestParams(name=tool, arguments={**arguments, "include_stack_trace": True})

    # Repeated failures/retries must not accumulate undisclosed sessions.
    for _ in range(2):
        with pytest.raises(server_module.MCPError, match=f"triage failed: {failure}"):
            await handler(None, params)
        assert created[-1].shutdown_calls == 1
        assert server_module._sessions == {"existing": unrelated}

    session_type.failure = None
    result = await handler(None, params)
    new_ids = set(server_module._sessions) - {"existing"}
    assert len(new_ids) == 1
    session_id = new_ids.pop()
    assert session_id in result.content[0].text
    assert server_module._sessions[session_id]["session"] is created[-1]
    assert created[-1].shutdown_calls == 0
    await handler(None, CallToolRequestParams(name=f"close_{kind}_session", arguments={"session_id": session_id}))
    assert created[-1].shutdown_calls == 1
    assert server_module._sessions == {"existing": unrelated}


@pytest.mark.anyio
async def test_cleanup_failure_does_not_hide_open_failure(open_handler):
    make_handler, session_type, created = open_handler
    session_type.failure = "!peb"
    session_type.shutdown_failure = True
    with pytest.raises(server_module.MCPError, match="triage failed: !peb"):
        await make_handler()(None, CallToolRequestParams(name="open_cdb_remote", arguments={"connection_string": "test remote"}))
    assert created[0].shutdown_calls == 1
    assert not server_module._sessions


@pytest.mark.anyio
@pytest.mark.parametrize("tool,arguments,first,second,kind", OPEN_CALLS)
async def test_output_filter_failure_rolls_back_open(open_handler, tool, arguments, first, second, kind):
    class FailingFilter:
        def process_input(self, tool_name, arguments, transport, call_id):
            return arguments

        def process_output(self, tool_name, content, transport, call_id):
            raise RuntimeError("output filter failed")

    make_handler, _, created = open_handler
    with pytest.raises(server_module.MCPError, match="output filter failed"):
        await make_handler(FailingFilter())(None, CallToolRequestParams(name=tool, arguments=arguments))
    assert created[0].shutdown_calls == 1
    assert not server_module._sessions
