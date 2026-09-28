"""Concurrent tool calls over MCP against a disposable local CDB target."""

from __future__ import annotations

import os
import re
import uuid

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from e2e import harness


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.live
@pytest.mark.remote
async def test_break_in_interrupts_command_over_mcp():
    if not harness.cdb_available():
        pytest.skip("cdb.exe not found")

    with harness.RemoteCdbServer(
        target=["waitfor.exe", "McpConcurrencyTestSignal"]
    ) as remote:
        command, args = harness.server_command([])
        params = StdioServerParameters(command=command, args=args, env=dict(os.environ))
        with anyio.fail_after(60):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as client:
                    await client.initialize()
                    opened = await client.call_tool(
                        "open_cdb_remote",
                        {"connection_string": remote.connection_string},
                    )
                    assert not opened.is_error
                    session_id = re.search(
                        r"session_id:\s*(cdb-[0-9a-f]+)", opened.content[0].text
                    ).group(1)
                    results = []
                    marker = "COMMAND_STARTED_" + uuid.uuid4().hex

                    async def run_command():
                        # A conditional resume waits for the next prompt, unlike
                        # a bare 'g' which the server deliberately returns from.
                        results.append(await client.call_tool(
                            "run_cdb_command",
                            {
                                "session_id": session_id,
                                "command": f".echo {marker}; .if (1) {{ g }}",
                                "timeout_seconds": 30,
                            },
                        ))

                    try:
                        async with anyio.create_task_group() as tasks:
                            tasks.start_soon(run_command)
                            with anyio.fail_after(10):
                                while not any(
                                    line.strip() == marker for line in remote.output_lines
                                ):
                                    await anyio.sleep(0.01)
                                # A separate request must finish while the
                                # command is still waiting on the running target.
                                await client.list_tools()
                                assert not results
                                with pytest.raises(MCPError, match="session is busy"):
                                    await client.call_tool(
                                        "run_cdb_command",
                                        {"session_id": session_id, "command": "r"},
                                    )
                                interrupted = await client.call_tool(
                                    "send_ctrl_break", {"session_id": session_id}
                                )
                                assert not interrupted.is_error

                        assert not results[0].is_error
                        assert "Break instruction exception" in results[0].content[0].text
                        followup = await client.call_tool(
                            "run_cdb_command",
                            {"session_id": session_id, "command": ".echo STILL_USABLE"},
                        )
                        assert not followup.is_error
                        assert "STILL_USABLE" in followup.content[0].text
                    finally:
                        await client.call_tool("close_cdb_session", {"session_id": session_id})
