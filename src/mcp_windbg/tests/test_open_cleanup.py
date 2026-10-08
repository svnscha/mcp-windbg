"""Failed opens must not leave debugger sessions the caller cannot address."""

from __future__ import annotations

import threading

import anyio
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
            self.shutdown_thread = threading.get_ident()
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
    with pytest.raises(server_module.MCPError) as raised:
        await make_handler()(None, CallToolRequestParams(name="open_cdb_remote", arguments={"connection_string": "test remote"}))
    # The primary message, not the whole string: str() on an MCPError embeds the
    # chained traceback, so a `match=` here passes even when the teardown error
    # has replaced the open error, which is the thing this test is about.
    primary = str(raised.value).splitlines()[0]
    assert "triage failed: !peb" in primary, primary
    assert created[0].shutdown_calls == 1
    assert not server_module._sessions


@pytest.mark.anyio
async def test_output_filter_keeps_event_loop_thread(open_handler):
    event_thread = threading.get_ident()

    class CheckingFilter:
        def process_input(self, tool_name, arguments, transport, call_id):
            assert threading.get_ident() == event_thread
            return arguments

        def process_output(self, tool_name, content, transport, call_id):
            assert threading.get_ident() == event_thread
            return content

    make_handler, _, created = open_handler
    result = await make_handler(CheckingFilter())(None, CallToolRequestParams(name="open_cdb_remote", arguments={"connection_string": "test remote"}))
    assert "session_id:" in result.content[0].text
    assert created[0].shutdown_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("tool,arguments,first,second,kind", OPEN_CALLS)
async def test_cancelled_open_rolls_back_after_worker_finishes(open_handler, monkeypatch, tool, arguments, first, second, kind):
    make_handler, session_type, created = open_handler
    started = threading.Event()
    release = threading.Event()
    send_command = session_type.send_command

    def blocking_command(self, command, timeout):
        if command == "!init":
            started.set()
            if not release.wait(2):
                raise RuntimeError("test failed to release open worker")
        return send_command(self, command, timeout)

    monkeypatch.setattr(session_type, "send_command", blocking_command)
    handler = make_handler()

    async def open_session():
        await handler(None, CallToolRequestParams(name=tool, arguments=arguments))

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(open_session)
            try:
                while not started.is_set():
                    await anyio.sleep(0)
                tasks.cancel_scope.cancel()
            finally:
                release.set()

    assert created[0].shutdown_calls == 1
    # The one place this is pinned: a shielded rollback still has to shut the
    # debugger down on a worker, because shutdown can block for seconds and the
    # event loop has to stay free for break-in on other sessions.
    assert created[0].shutdown_thread != threading.get_ident()
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


@pytest.mark.anyio
@pytest.mark.parametrize("tool,arguments,first,second,kind", OPEN_CALLS)
async def test_rollback_does_not_resume_the_target(open_handler, tool, arguments, first, second, kind):
    """A rolled-back open must leave the target as it found it.

    Closing a session normally lets a live target run again, which is what
    ``close_kd_session``'s ``resume`` parameter is for. An open that failed
    never handed out a session id, so resuming on its behalf would be a
    side effect nobody asked for and could not opt out of.
    """
    make_handler, session_type, created = open_handler
    session_type.failure = first
    handler = make_handler()

    with pytest.raises(server_module.MCPError, match=f"triage failed: {first}"):
        await handler(None, CallToolRequestParams(name=tool, arguments=arguments))

    assert created[0].shutdown_calls == 1
    assert created[0].resume_on_close is False
    assert not server_module._sessions


@pytest.mark.anyio
async def test_a_close_that_fails_to_shut_down_reports_it(open_handler):
    """A close used to swallow the shutdown error and still report success.

    The record is dropped either way, so the id cannot be retried, and a
    debugger left holding its target is exactly what the caller needs to know
    about.
    """
    make_handler, session_type, created = open_handler
    handler = make_handler()
    await handler(None, CallToolRequestParams(
        name="open_cdb_dump", arguments={"dump_path": "app.dmp"}
    ))
    session_id = next(iter(server_module._sessions))
    created[0].shutdown_failure = True

    with pytest.raises(server_module.MCPError, match="cleanup failure"):
        await handler(None, CallToolRequestParams(
            name="close_cdb_session", arguments={"session_id": session_id}
        ))

    assert not server_module._sessions


@pytest.mark.anyio
async def test_list_dumps_keeps_a_stable_order(open_handler, tmp_path):
    """The order is part of what callers see, so it stays sorted rather than
    following whatever order the filesystem hands back."""
    for name in ["zz.dmp", "B.dmp", "a.dmp", "Mid.dmp", "Z0.dmp"]:
        (tmp_path / name).write_bytes(b"x")
    make_handler, _, _ = open_handler

    result = await make_handler()(None, CallToolRequestParams(
        name="list_dumps", arguments={"directory_path": str(tmp_path)}
    ))

    text = result.content[0].text if hasattr(result, "content") else result[0].text
    listed = [n for n in ["B.dmp", "Mid.dmp", "Z0.dmp", "a.dmp", "zz.dmp"] if n in text]
    positions = [text.index(n) for n in listed]
    assert positions == sorted(positions), f"not in sorted order: {listed}"
    assert len(listed) == 5


@pytest.mark.anyio
async def test_a_failed_rollback_does_not_replace_the_open_error(monkeypatch):
    """Why the open failed is what the caller can act on.

    If tearing the debugger down then fails too, that error must not take the
    place of the original: the record is gone either way, so there is no id to
    retry with, and "cleanup failure" says nothing about why the open did not
    work. Deliberately built on its own fake rather than the shared fixture,
    because this has to fail when the rollback error is left to propagate.
    """
    monkeypatch.setattr(server_module, "_sessions", {})

    class Session:
        def __init__(self, **kwargs):
            self.shutdown_calls = 0

        def send_command(self, command, timeout):
            if command == "!peb":
                raise DebuggerError("triage failed: !peb")
            return ["output"]

        def shutdown(self):
            self.shutdown_calls += 1
            raise RuntimeError("cleanup failure")

    monkeypatch.setattr(server_module, "CDBSession", Session)
    captured = {}
    real_server = server_module.Server
    monkeypatch.setattr(
        server_module,
        "Server",
        lambda *a, **k: (captured.update(k), real_server(*a, **k))[1],
    )
    server_module._create_server(init_commands=["!init"])
    handler = captured["on_call_tool"]

    with pytest.raises(server_module.MCPError) as raised:
        await handler(None, CallToolRequestParams(
            name="open_cdb_remote", arguments={"connection_string": "test remote"}
        ))

    # The primary message only. str() on an MCPError embeds the chained
    # traceback, so searching the whole string finds "triage failed" even when
    # the teardown error has taken its place.
    primary = str(raised.value).splitlines()[0]
    assert "triage failed: !peb" in primary, (
        f"the teardown failure replaced the open error: {primary}"
    )
    assert "cleanup failure" not in primary
    assert not server_module._sessions
