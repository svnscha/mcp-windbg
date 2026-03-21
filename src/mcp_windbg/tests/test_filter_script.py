from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

import mcp_windbg.server as windbg_server

from mcp_windbg.filter_script import load_filter_script


def test_load_filter_script_requires_callback(tmp_path: Path):
    """A script with neither process_input nor process_output is rejected."""
    script_path = tmp_path / "empty_filter.py"
    script_path.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="process_input"):
        load_filter_script(str(script_path))


def test_load_filter_script_file_not_found():
    """A nonexistent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_filter_script("/nonexistent/path/filter.py")


def test_filter_script_single_arg_callbacks(tmp_path: Path):
    """Scripts with single-arg callbacks (no context) are supported."""
    script_path = tmp_path / "simple_filter.py"
    script_path.write_text(
        "def process_output(text):\n"
        "    return text.upper()\n",
        encoding="utf-8",
    )

    fs = load_filter_script(str(script_path))
    result = fs.process_output("run_windbg_cmd", [TextContent(type="text", text="hello")], "stdio", "id1")
    assert result[0].text == "HELLO"


def test_filter_script_output_only(tmp_path: Path):
    """A script defining only process_output leaves input untouched."""
    script_path = tmp_path / "output_only.py"
    script_path.write_text(
        "def process_output(text, context):\n"
        "    return text + ' [redacted]'\n",
        encoding="utf-8",
    )

    fs = load_filter_script(str(script_path))
    # process_input should pass through unchanged
    args = {"command": "k", "dump_path": "test.dmp"}
    assert fs.process_input("run_windbg_cmd", args, "stdio", "id1") == args
    # process_output should append
    result = fs.process_output("run_windbg_cmd", [TextContent(type="text", text="stack")], "stdio", "id1")
    assert result[0].text == "stack [redacted]"


def test_filter_script_input_only(tmp_path: Path):
    """A script defining only process_input leaves output untouched."""
    script_path = tmp_path / "input_only.py"
    script_path.write_text(
        "def process_input(text, context):\n"
        "    return text.replace('secret', '[REDACTED]')\n",
        encoding="utf-8",
    )

    fs = load_filter_script(str(script_path))
    args = {"command": "echo secret", "count": 42, "verbose": True}
    result = fs.process_input("run_windbg_cmd", args, "stdio", "id1")
    # String values are transformed, non-strings are preserved
    assert result["command"] == "echo [REDACTED]"
    assert result["count"] == 42
    assert result["verbose"] is True
    # process_output should pass through unchanged
    content = [TextContent(type="text", text="original")]
    assert fs.process_output("run_windbg_cmd", content, "stdio", "id1")[0].text == "original"


def test_filter_script_callback_error(tmp_path: Path):
    """A filter script that raises an exception produces a RuntimeError."""
    script_path = tmp_path / "bad_filter.py"
    script_path.write_text(
        "def process_output(text, context):\n"
        "    raise ValueError('boom')\n",
        encoding="utf-8",
    )

    fs = load_filter_script(str(script_path))
    with pytest.raises(RuntimeError, match="boom"):
        fs.process_output("run_windbg_cmd", [TextContent(type="text", text="hi")], "stdio", "id1")


def test_filter_script_rewrites_tool_content(tmp_path: Path, monkeypatch):
    """End-to-end: filter rewrites tool args and output through a real MCP server."""
    script_path = tmp_path / "redact_filter.py"
    script_path.write_text(
        "def process_input(text, context):\n"
        "    global input_call_id\n"
        "    input_call_id = context['call_id']\n"
        "    if context['tool_name'] == 'run_windbg_cmd' and context['argument_path'] == '$.command':\n"
        "        return text.replace('secret', '[redacted]')\n"
        "    return text\n\n"
        "def process_output(text, context):\n"
        "    assert context['call_id'] == input_call_id\n"
        "    if context['tool_name'] == 'run_windbg_cmd':\n"
        "        return text + ' [filtered]'\n"
        "    return text\n",
        encoding="utf-8",
    )

    captured: dict[str, str] = {}

    class FakeSession:
        def send_command(self, command: str) -> list[str]:
            captured["command"] = command
            return [f"processed {command}"]

    monkeypatch.setattr(windbg_server, "get_or_create_session", lambda **kwargs: FakeSession())

    content_filter = load_filter_script(str(script_path))
    server = windbg_server._create_server(
        timeout=30,
        verbose=False,
        content_filter=content_filter,
        transport="memory",
    )

    async def run_test() -> None:
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("run_windbg_cmd", {"dump_path": "demo.dmp", "command": "echo secret"})
            assert result.isError is False
            assert captured["command"] == "echo [redacted]"
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "Command: echo [redacted]\n\nOutput:\n```\nprocessed echo [redacted]\n``` [filtered]"

    anyio.run(run_test)
