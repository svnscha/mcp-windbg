"""Run a declarative scenario against a really-hosted mcp-windbg server.

The runner spawns ``python -m mcp_windbg`` as a subprocess and connects to it
with a real MCP ``ClientSession`` over stdio. Each scenario step is a direct
tool call, prompt fetch, or tools listing - exactly what an LLM would issue,
except the calls come from the YAML instead of a model. Nothing else is faked:
real server, real transport, real ``cdb.exe``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import anyio
import pytest
import yaml
from anyio import BrokenResourceError, ClosedResourceError

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from . import harness

if sys.version_info < (3, 11):  # BaseExceptionGroup is a builtin from 3.11 on
    from exceptiongroup import BaseExceptionGroup

# Generous default; opening a dump and running !analyze -v can take a while.
DEFAULT_TIMEOUT = 180

# Stream-cleanup errors the MCP client can raise during teardown when the server
# emits a trailing stdout line while the client is closing its read stream.
#
# Root cause is an upstream SDK race: mcp.client.stdio.stdio_client's
# stdout_reader catches anyio.ClosedResourceError but not BrokenResourceError
# around its read_stream_writer.send(), so a send into a just-closed receiver
# escapes as a BrokenResourceError inside the task group's ExceptionGroup.
# Confirmed still present in mcp 1.27.2 (the latest at the time of writing), so
# bumping the dependency does not fix it. We suppress it only after every step
# has completed, so a real failure (which raises before `completed` is set) is
# never masked.
_TEARDOWN_NOISE = (BrokenResourceError, ClosedResourceError)


def _is_teardown_noise(exc: BaseException) -> bool:
    """True if exc is only transport-teardown noise, not a real test failure."""
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(_is_teardown_noise(e) for e in exc.exceptions)
    return isinstance(exc, _TEARDOWN_NOISE)


def load_scenario(path: Path) -> dict[str, Any]:
    """Load a scenario YAML file into a plain dict, tagged with its path."""
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data.setdefault("name", path.stem)
    data["_path"] = str(path)
    return data


def _requires(scenario: dict[str, Any]) -> dict[str, Any]:
    return scenario.get("requires") or {}


def _remote_spec(scenario: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Normalize the ``requires.remote`` field to a dict or None."""
    remote = _requires(scenario).get("remote")
    if not remote:
        return None
    if remote is True:
        return {}
    if isinstance(remote, dict):
        return remote
    raise ValueError(f"requires.remote must be a boolean or mapping, got {remote!r}")


def scenario_markers(scenario: dict[str, Any]) -> set[str]:
    """Pytest markers implied by a scenario's requirements (for conftest)."""
    requires = _requires(scenario)
    markers: set[str] = set()
    if requires.get("cdb") or _remote_spec(scenario) is not None or requires.get("kernel"):
        markers.add("live")
    if _remote_spec(scenario) is not None:
        markers.add("remote")
    if requires.get("kernel"):
        markers.add("kernel")
    return markers


def skip_reason(scenario: dict[str, Any]) -> Optional[str]:
    """Why this scenario should be skipped on this machine, or None to run.

    Only a missing debugger or an unconfigured kernel target causes a skip. The
    Git LFS dumps are mandatory - a scenario that needs one hard-fails (see
    run_scenario) rather than skipping, so a repo without `git lfs pull` is a
    loud error, not silent green.
    """
    requires = _requires(scenario)
    needs_cdb = bool(requires.get("cdb")) or _remote_spec(scenario) is not None
    if needs_cdb and not harness.cdb_available():
        return "cdb.exe not found"
    if requires.get("kernel"):
        if harness.find_kd() is None:
            return "kd.exe not found"
        if harness.kernel_connection() is None:
            return f"no kernel target configured ({harness.KERNEL_CONNECTION_ENV} unset)"
    return None


async def run_scenario(scenario: dict[str, Any]) -> None:
    """Execute a scenario end-to-end. Raises AssertionError on a failed step."""
    reason = skip_reason(scenario)
    if reason:
        pytest.skip(reason)

    timeout = scenario.get("timeout", DEFAULT_TIMEOUT)
    remote_spec = _remote_spec(scenario)
    remote_server: Optional[harness.RemoteCdbServer] = None

    # Placeholders available to every argument string in the scenario.
    mapping = {
        "dumps_dir": str(harness.DUMPS_DIR),
        "scenarios_dir": str(harness.SCENARIOS_DIR),
    }
    dump = _requires(scenario).get("dump")
    if dump:
        dump_path = harness.dump_file(dump)
        assert dump_path.exists(), (
            f"required dump '{dump}' is missing at {dump_path}; the LFS dumps are "
            f"mandatory - run `git lfs pull`"
        )
        mapping["dump"] = str(dump_path)
    cdb = harness.find_cdb()
    if cdb:
        mapping["cdb"] = cdb
    kd = harness.find_kd()
    if kd:
        mapping["kd"] = kd
    kernel = harness.kernel_connection()
    if kernel:
        mapping["kernel"] = kernel

    try:
        if remote_spec is not None:
            remote_server = harness.RemoteCdbServer(target=remote_spec.get("target"))
            remote_server.start()
            mapping["remote"] = remote_server.connection_string

        server_args = harness.resolve_placeholders(
            (scenario.get("server") or {}).get("args", []), mapping
        )

        if scenario.get("negative_launch"):
            command, args = harness.server_command(server_args)
            await _expect_launch_failure(
                StdioServerParameters(command=command, args=args, env=dict(os.environ)),
                timeout,
            )
            return

        if scenario.get("transport") == "streamable-http":
            await _run_http(scenario, server_args, remote_server, mapping, timeout)
        else:
            await _run_stdio(scenario, server_args, remote_server, mapping, timeout)
    finally:
        if remote_server is not None:
            remote_server.cleanup()


async def _run_stdio(scenario, server_args, remote_server, mapping, timeout) -> None:
    command, args = harness.server_command(server_args)
    params = StdioServerParameters(
        command=command,
        args=args,
        env=dict(os.environ),  # keep LOCALAPPDATA etc. so cdb discovery works
    )
    completed = False
    try:
        with anyio.fail_after(timeout):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await _run_steps(scenario, session, remote_server, mapping)
                    completed = True
    except BaseException as exc:
        # Ignore stream-cleanup noise that surfaces only after every step ran.
        if completed and _is_teardown_noise(exc):
            return
        raise


async def _run_http(scenario, server_args, remote_server, mapping, timeout) -> None:
    """Host the server over the streamable-http transport and drive it over HTTP.

    This is a behavioral test of the transport. The HTTP server cannot flush
    coverage on Windows teardown (asyncio's Proactor loop does not deliver the
    shutdown signal, so the process is hard-killed), which is why serve_http is
    excluded from coverage in pyproject.toml.
    """
    port = harness.free_port()
    command, args = harness.server_command(
        ["--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(port), *server_args]
    )
    process = subprocess.Popen([command, *args], env=dict(os.environ))
    completed = False
    try:
        if not harness.wait_for_port(port, timeout=min(timeout, 30)):
            raise AssertionError(f"[{scenario['name']}]: HTTP server never bound to port {port}")
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            with anyio.fail_after(timeout):
                async with streamable_http_client(url) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        await _run_steps(scenario, session, remote_server, mapping)
                        completed = True
        except BaseException as exc:
            if not (completed and _is_teardown_noise(exc)):
                raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


async def _run_steps(scenario, session, remote_server, mapping) -> None:
    for index, step in enumerate(scenario.get("steps", [])):
        await _run_step(scenario, index, step, session, remote_server, mapping)


async def _expect_launch_failure(params: StdioServerParameters, timeout: int) -> None:
    """Assert the hosted server fails to come up (used for bad --filter-script)."""
    raised = False
    try:
        with anyio.fail_after(timeout):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
    except Exception:
        raised = True
    assert raised, "expected the server to fail to launch, but it started cleanly"


async def _run_step(
    scenario: dict[str, Any],
    index: int,
    step: dict[str, Any],
    session: ClientSession,
    remote_server: Optional[harness.RemoteCdbServer],
    mapping: dict[str, str],
) -> None:
    expect = step.get("expect") or {}
    arguments = harness.resolve_placeholders(step.get("arguments") or {}, mapping)

    if "call" in step:
        tool = step["call"]
        is_error, text = await _call_tool(session, tool, arguments)
        _check(scenario, index, f"call {tool}", expect, text, is_error)
        _apply_capture(scenario, index, step, text, mapping)
        return

    if "get_prompt" in step:
        name = step["get_prompt"]
        try:
            result = await session.get_prompt(name, arguments or None)
        except McpError as exc:
            _check(scenario, index, f"get_prompt {name}", expect, str(exc), is_error=True)
            return
        text = "\n".join(
            getattr(message.content, "text", "") for message in result.messages
        )
        text = f"{result.description or ''}\n{text}"
        _check(scenario, index, f"get_prompt {name}", expect, text, is_error=False)
        return

    if "list_tools" in step:
        result = await session.list_tools()
        text = "\n".join(tool.name for tool in result.tools)
        _check(scenario, index, "list_tools", expect, text, is_error=False)
        return

    if "server_input" in step:
        if remote_server is None:
            raise AssertionError(
                _label(scenario, index, "server_input")
                + ": step requires a remote server but none is running"
            )
        remote_server.write(harness.resolve_placeholders(step["server_input"], mapping))
        time.sleep(step.get("wait", 1.0))
        return

    raise AssertionError(
        _label(scenario, index, "?")
        + f": unknown step kind (keys: {sorted(step)})"
    )


def _apply_capture(
    scenario: dict[str, Any],
    index: int,
    step: dict[str, Any],
    text: str,
    mapping: dict[str, str],
) -> None:
    """Store regex captures from a step's output for later placeholders.

    ``capture: {name: pattern}`` searches the step output; group(1) (or the whole
    match) becomes ``{name}`` in subsequent steps. This is how a scenario threads
    an opaque session_id returned by an open_* call into later run_/close_ calls -
    exactly what an LLM does when it reads the id and reuses it.
    """
    import re

    for name, pattern in (step.get("capture") or {}).items():
        match = re.search(pattern, text)
        if not match:
            raise AssertionError(
                _label(scenario, index, "capture")
                + f": pattern /{pattern}/ for {name!r} not found\n--- output ---\n{text[:1000]}"
            )
        mapping[name] = match.group(1) if match.groups() else match.group(0)


async def _call_tool(session: ClientSession, tool: str, arguments: dict[str, Any]):
    """Call a tool, normalizing both isError results and raised McpErrors."""
    try:
        result = await session.call_tool(tool, arguments)
    except McpError as exc:
        return True, str(exc)
    text = "\n".join(getattr(item, "text", "") for item in result.content)
    return bool(result.isError), text


def _label(scenario: dict[str, Any], index: int, kind: str) -> str:
    return f"[{scenario['name']}] step {index} ({kind})"


def _check(
    scenario: dict[str, Any],
    index: int,
    kind: str,
    expect: dict[str, Any],
    text: str,
    is_error: bool,
) -> None:
    label = _label(scenario, index, kind)
    preview = text if len(text) <= 2000 else text[:2000] + "\n...[truncated]"

    # A tool call must not error unless the scenario says so.
    if "isError" in expect:
        if bool(expect["isError"]) != is_error:
            raise AssertionError(
                f"{label}: expected isError={expect['isError']}, got {is_error}\n--- output ---\n{preview}"
            )
    elif is_error and kind.startswith("call "):
        raise AssertionError(f"{label}: unexpected tool error\n--- output ---\n{preview}")

    for needle in expect.get("contains", []):
        if needle not in text:
            raise AssertionError(f"{label}: expected to contain {needle!r}\n--- output ---\n{preview}")

    for needle in expect.get("not_contains", []):
        if needle in text:
            raise AssertionError(f"{label}: expected NOT to contain {needle!r}\n--- output ---\n{preview}")

    import re

    for pattern in expect.get("regex", []):
        if not re.search(pattern, text):
            raise AssertionError(f"{label}: expected to match /{pattern}/\n--- output ---\n{preview}")
