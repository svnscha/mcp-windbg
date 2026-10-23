"""The --log-dir session log and read_debugger_log, against fake sessions.

The real reader thread writing the log is covered in test_debug_session.py,
and a real cdb writing one in scenarios/debugger_log.yaml.
"""

from __future__ import annotations

import pytest
from mcp.types import CallToolRequestParams

from mcp_windbg import server as server_module


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(server_module, "_sessions", {})
    created = []

    class Session:
        def __init__(self, **kwargs):
            self.log_path = kwargs.get("session_log_path")
            created.append(self)

        def send_command(self, command, timeout):
            return [f"output: {command}"]

        def shutdown(self):
            pass

    monkeypatch.setattr(server_module, "CDBSession", Session)
    monkeypatch.setattr(server_module, "KDSession", Session)
    captured = {}
    real_server = server_module.Server

    def record_handlers(*args, **kwargs):
        captured.update(kwargs)
        return real_server(*args, **kwargs)

    monkeypatch.setattr(server_module, "Server", record_handlers)

    def make(log_dir=None):
        server_module._create_server(log_dir=log_dir)
        return captured["on_call_tool"]

    return make, created


def _read(session_id, **arguments):
    return CallToolRequestParams(
        name="read_debugger_log", arguments={"session_id": session_id, **arguments}
    )


@pytest.mark.anyio
async def test_each_session_logs_to_a_file_named_after_its_id(server, tmp_path):
    make, created = server
    handler = make(str(tmp_path / "logs"))  # created at startup
    await handler(None, CallToolRequestParams(name="open_cdb_dump", arguments={"dump_path": "app.dmp"}))
    await handler(None, CallToolRequestParams(name="open_kd_dump", arguments={"dump_path": "MEMORY.DMP"}))

    ids = list(server_module._sessions)
    assert [s.log_path for s in created] == [str(tmp_path / "logs" / f"{i}.log") for i in ids]


@pytest.mark.anyio
async def test_without_a_log_dir_nothing_is_logged(server):
    make, created = server
    handler = make()
    await handler(None, CallToolRequestParams(name="open_cdb_dump", arguments={"dump_path": "app.dmp"}))
    assert created[0].log_path is None

    with pytest.raises(server_module.MCPError, match="--log-dir"):
        await handler(None, _read("cdb-0123abcd"))


@pytest.mark.anyio
async def test_read_returns_the_newest_matching_lines(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "MAX_RETAINED_LINE_CHARS", 20)
    make, _ = server
    handler = make(str(tmp_path))
    (tmp_path / "kd-0123abcd.log").write_text(
        "".join(f"10:00:0{i} line {i} {'HIT' if i % 2 else 'miss'}\n" for i in range(8))
        + "10:00:09 HIT " + "x" * 50 + "\n",
        encoding="utf-8",
    )

    result = await handler(None, _read("kd-0123abcd", lines=3, grep="hit"))
    text = result.content[0].text
    assert "last 3 lines matching 'hit'" in text
    assert "line 5 HIT" in text and "line 7 HIT" in text
    assert "line 3" not in text and "miss" not in text
    assert "10:00:09 HIT xxxxxxx ... line truncated ..." in text


@pytest.mark.anyio
@pytest.mark.parametrize("session_id", ["..\\secret", "cdb-0123abcd.log", "C:\\x", "cdb-XYZ"])
async def test_read_only_takes_a_session_id(server, tmp_path, session_id):
    make, _ = server
    with pytest.raises(server_module.MCPError, match="Not a session_id"):
        await make(str(tmp_path))(None, _read(session_id))


@pytest.mark.anyio
async def test_read_reports_a_session_without_a_log(server, tmp_path):
    make, _ = server
    with pytest.raises(server_module.MCPError, match="No debugger log for session cdb-0123abcd"):
        await make(str(tmp_path))(None, _read("cdb-0123abcd"))
