"""The registered tool handler must not block unrelated calls on debugger I/O."""

from __future__ import annotations

import threading

import anyio
import pytest
from mcp.types import CallToolRequestParams

from mcp_windbg import server as server_module


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tool_handler(monkeypatch):
    captured = {}
    real_server = server_module.Server

    def record_handlers(*args, **kwargs):
        captured.update(kwargs)
        return real_server(*args, **kwargs)

    monkeypatch.setattr(server_module, "Server", record_handlers)
    monkeypatch.setattr(server_module, "_sessions", {})
    server_module._create_server()
    return captured["on_call_tool"]


class BlockingSession:
    is_live_session = True

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.thread_id = None

    def send_command(self, command, timeout):
        self.thread_id = threading.get_ident()
        self.started.set()
        if not self.release.wait(1):
            raise RuntimeError("The event loop could not dispatch the break-in")
        return ["completed"]

    def send_ctrl_break(self):
        self.release.set()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["cdb", "kd"])
async def test_blocked_command_can_be_interrupted_by_another_tool(tool_handler, kind):
    session = BlockingSession()
    server_module._sessions["test-session"] = {
        "session": session,
        "kind": kind,
        "label": "test target",
    }
    results = []
    limiter = anyio.to_thread.current_default_thread_limiter()
    old_limit = limiter.total_tokens
    limiter.total_tokens = 1

    async def command():
        results.append(
            await tool_handler(
                None,
                CallToolRequestParams(
                    name=f"run_{kind}_command",
                    arguments={
                        "session_id": "test-session",
                        "command": "kb",
                        "timeout_seconds": 10,
                    },
                ),
            )
        )

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(command)
            with anyio.fail_after(2):
                while not session.started.is_set():
                    await anyio.sleep(0)
                interrupted = await tool_handler(
                    None,
                    CallToolRequestParams(
                        name="send_ctrl_break",
                        arguments={"session_id": "test-session"},
                    ),
                )
                assert "Sent CTRL+BREAK" in interrupted.content[0].text
    finally:
        session.release.set()
        limiter.total_tokens = old_limit

    assert session.thread_id != threading.get_ident()
    assert "completed" in results[0].content[0].text


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["cdb", "kd"])
async def test_command_errors_keep_mcp_error_conversion(tool_handler, kind):
    class FailingSession:
        def send_command(self, command, timeout):
            raise RuntimeError("test command failure")

    server_module._sessions["test-session"] = {
        "session": FailingSession(),
        "kind": kind,
        "label": "test target",
    }
    with pytest.raises(server_module.MCPError, match="test command failure"):
        await tool_handler(
            None,
            CallToolRequestParams(
                name=f"run_{kind}_command",
                arguments={"session_id": "test-session", "command": "kb"},
            ),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("open_cdb_dump", {"dump_path": "test.dmp"}),
        ("open_cdb_remote", {"connection_string": "test remote"}),
        ("open_kd_session", {"connection_string": "test kernel"}),
    ],
)
async def test_startup_keeps_other_tools_available(
    tool_handler, monkeypatch, tmp_path, tool, arguments
):
    started = threading.Event()
    release = threading.Event()
    thread_ids = []

    class StartupSession:
        def __init__(self, **kwargs):
            thread_ids.append(threading.get_ident())
            started.set()
            if not release.wait(1):
                raise RuntimeError("Startup blocked the event loop")

        def send_command(self, command, timeout):
            return ["ready"]

    monkeypatch.setattr(server_module, "CDBSession", StartupSession)
    monkeypatch.setattr(server_module, "KDSession", StartupSession)
    results = []

    async def open_session():
        results.append(
            await tool_handler(
                None, CallToolRequestParams(name=tool, arguments=arguments)
            )
        )

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(open_session)
            with anyio.fail_after(2):
                while not started.is_set():
                    await anyio.sleep(0)
                result = await tool_handler(
                    None,
                    CallToolRequestParams(
                        name="list_dumps",
                        arguments={"directory_path": str(tmp_path)},
                    ),
                )
                assert "No crash dump" in result.content[0].text
                release.set()
    finally:
        release.set()

    assert len(thread_ids) == 1
    assert thread_ids[0] != threading.get_ident()
    assert "Opened" in results[0].content[0].text


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["cdb", "kd"])
async def test_concurrent_close_claims_session_once(tool_handler, kind):
    started = threading.Event()
    release = threading.Event()
    calls = []

    class ClosingSession:
        resume_on_close = True

        def shutdown(self):
            calls.append((self.resume_on_close, threading.get_ident()))
            started.set()
            if not release.wait(1):
                raise RuntimeError("Shutdown blocked the event loop")

    server_module._sessions["test-session"] = {
        "session": ClosingSession(),
        "kind": kind,
        "label": "test target",
    }
    results = []

    async def close_session():
        arguments = {"session_id": "test-session"}
        if kind == "kd":
            arguments["resume"] = False
        results.append(
            await tool_handler(
                None,
                CallToolRequestParams(
                    name=f"close_{kind}_session",
                    arguments=arguments,
                ),
            )
        )

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(close_session)
            with anyio.fail_after(2):
                while not started.is_set():
                    await anyio.sleep(0)
                second = await tool_handler(
                    None,
                    CallToolRequestParams(
                        name=f"close_{kind}_session",
                        arguments={"session_id": "test-session"},
                    ),
                )
                assert "No active" in second.content[0].text
                release.set()
    finally:
        release.set()

    assert len(calls) == 1
    assert calls[0][0] == (kind != "kd")
    assert calls[0][1] != threading.get_ident()
    assert "Successfully closed" in results[0].content[0].text
    assert not server_module._sessions


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["cdb", "kd"])
async def test_break_in_racing_with_close_reports_debugger_error(tool_handler, kind):
    class ClosingSession:
        @property
        def is_live_session(self):
            # Force shutdown between validating the session and signalling it.
            closer = threading.Thread(
                target=server_module._close_session, args=("test-session", kind)
            )
            closer.start()
            closer.join(2)
            assert not closer.is_alive()
            return True

        def shutdown(self):
            pass

        def send_ctrl_break(self):
            raise RuntimeError("Debugger process is not running")

    server_module._sessions["test-session"] = {
        "session": ClosingSession(),
        "kind": kind,
        "label": "test target",
    }
    with pytest.raises(server_module.MCPError, match="Debugger process is not running"):
        await tool_handler(
            None,
            CallToolRequestParams(
                name="send_ctrl_break", arguments={"session_id": "test-session"}
            ),
        )
