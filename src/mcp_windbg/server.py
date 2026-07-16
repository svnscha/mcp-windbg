import os
import traceback
import glob
import winreg
import logging
import uuid
from typing import Dict, Optional
from contextlib import asynccontextmanager

from .cdb_session import CDBSession
from .kd_session import KDSession
from .filter_script import FilterScript, load_filter_script
from .prompts import load_prompt

from mcp.shared.exceptions import McpError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    ErrorData,
    TextContent,
    Tool,
    Prompt,
    PromptArgument,
    PromptMessage,
    GetPromptResult,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --- Per-tool-call timeout defaults (seconds) --------------------------------
# A tool's effective timeout is: the call's `timeout_seconds` if given, else the
# larger of the tool default below and the server-wide `--timeout` floor. These
# defaults reflect how long each operation realistically takes:
#   - a dump's !analyze -v can run for a while,
#   - a KDNET memory read can be slow, especially on a flaky link.
CDB_DUMP_OPEN_TIMEOUT = 180
CDB_REMOTE_OPEN_TIMEOUT = 60
KD_OPEN_TIMEOUT = 60
CDB_COMMAND_TIMEOUT = 60
KD_COMMAND_TIMEOUT = 120


def _effective_timeout(per_call: Optional[int], tool_default: int, server_timeout: int) -> int:
    """Resolve a call's timeout: explicit override, else max(default, floor)."""
    if per_call and per_call > 0:
        return per_call
    return max(tool_default, server_timeout)


# --- Session registry --------------------------------------------------------
# Every open_* tool creates a session and returns an opaque session_id; every
# other tool addresses a session by that id. A record tracks the live session
# object, its kind ("cdb" or "kd"), and a human label for messages.
_sessions: Dict[str, dict] = {}


def _new_session_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:8]}"


def _register_session(session, kind: str, label: str) -> str:
    session_id = _new_session_id(kind)
    _sessions[session_id] = {"session": session, "kind": kind, "label": label}
    return session_id


def _require_session(session_id: str, kind: str):
    """Return the session for ``session_id``, or raise a helpful McpError.

    Enforces that the session is of the expected kind so ``run_cdb_command`` on a
    kernel session (or vice versa) fails clearly instead of misbehaving.
    """
    record = _sessions.get(session_id)
    if record is None:
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message=(
                f"Unknown session_id {session_id!r}. Open a session first - the "
                f"open_* tools return a session_id to use here."
            ),
        ))
    if record["kind"] != kind:
        actual = record["kind"]
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message=(
                f"session_id {session_id!r} is a {actual} session, not {kind}. "
                f"Use run_{actual}_command / close_{actual}_session for it."
            ),
        ))
    return record["session"]


def _close_session(session_id: str, kind: str) -> bool:
    """Shut down and forget a session; returns False if id/kind did not match."""
    record = _sessions.get(session_id)
    if record is None or record["kind"] != kind:
        return False
    try:
        record["session"].shutdown()
    except Exception:
        pass
    finally:
        _sessions.pop(session_id, None)
    return True


def get_local_dumps_path() -> Optional[str]:
    """Get the local dumps path from the Windows registry."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps"
        ) as key:
            dump_folder, _ = winreg.QueryValueEx(key, "DumpFolder")
            if os.path.exists(dump_folder) and os.path.isdir(dump_folder):
                return dump_folder
    except (OSError, WindowsError):
        # Registry key might not exist or other issues
        pass

    # Default Windows dump location
    default_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CrashDumps")
    if os.path.exists(default_path) and os.path.isdir(default_path):
        return default_path

    return None


# --- Tool parameter models ---------------------------------------------------

class ListDumps(BaseModel):
    """Parameters for listing crash dumps in a directory."""
    directory_path: Optional[str] = Field(
        default=None,
        description="Directory to search for dump files. Defaults to the configured dump path from the registry."
    )
    recursive: bool = Field(default=False, description="Search subdirectories recursively.")


class OpenCdbDump(BaseModel):
    """Parameters for opening a crash dump (user mode, cdb.exe)."""
    dump_path: str = Field(description="Path to the Windows crash dump file")
    symbols_path: Optional[str] = Field(default=None, description="Additional symbol search path for PDB resolution.")
    include_stack_trace: bool = Field(default=False, description="Include a stack trace (kb) in the initial analysis.")
    include_modules: bool = Field(default=False, description="Include loaded modules (lm) in the initial analysis.")
    include_threads: bool = Field(default=False, description="Include threads (~) in the initial analysis.")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the timeout (seconds) for opening/analyzing this dump.")


class OpenCdbRemote(BaseModel):
    """Parameters for attaching to a user-mode remote debug server (-remote)."""
    connection_string: str = Field(description="Remote debug-server string, e.g. 'tcp:Port=5005,Server=192.168.0.100'")
    symbols_path: Optional[str] = Field(default=None, description="Additional symbol search path for PDB resolution.")
    include_stack_trace: bool = Field(default=False, description="Include a stack trace (kb) in the initial output.")
    include_modules: bool = Field(default=False, description="Include loaded modules (lm) in the initial output.")
    include_threads: bool = Field(default=False, description="Include threads (~) in the initial output.")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the connect timeout (seconds).")


class OpenKdSession(BaseModel):
    """Parameters for attaching to a kernel target (-k, kd.exe)."""
    connection_string: str = Field(description="Kernel connection string: KDNET 'net:port=50000,key=1.2.3.4', named pipe 'com:pipe,port=\\\\.\\pipe\\com_1,baud=115200', or serial 'com:port=COM1,baud=115200'.")
    symbols_path: Optional[str] = Field(default=None, description="Additional symbol search path for PDB resolution.")
    include_stack_trace: bool = Field(default=False, description="Include a stack trace (kb) in the initial output.")
    include_modules: bool = Field(default=False, description="Include loaded modules (lm) in the initial output.")
    include_threads: bool = Field(default=False, description="Include threads (~) in the initial output.")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the connect/break-in timeout (seconds).")


class RunCdbCommand(BaseModel):
    """Parameters for running a command on a user-mode (cdb) session."""
    session_id: str = Field(description="A cdb session_id returned by open_cdb_dump or open_cdb_remote.")
    command: str = Field(description="WinDbg/CDB command to execute (e.g. 'kb', 'lm', '!analyze -v').")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the command timeout (seconds).")


class RunKdCommand(BaseModel):
    """Parameters for running a command on a kernel (kd) session."""
    session_id: str = Field(description="A kd session_id returned by open_kd_session.")
    command: str = Field(description="Kernel debugger command to execute (e.g. '!process 0 0', 'vertarget', '!analyze -v').")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the command timeout (seconds).")


class CloseCdbSession(BaseModel):
    """Parameters for closing a user-mode (cdb) session."""
    session_id: str = Field(description="The cdb session_id to close.")


class CloseKdSession(BaseModel):
    """Parameters for closing a kernel (kd) session."""
    session_id: str = Field(description="The kd session_id to close.")
    resume: bool = Field(default=True, description="Resume the target machine on close (send 'g' so it runs again). Set false to intentionally leave it halted at the break - note that freezes the whole machine until a debugger resumes it.")


class SendCtrlBreak(BaseModel):
    """Parameters for breaking into a running session."""
    session_id: str = Field(description="A live session_id (cdb remote or kd) to break into.")


def _combine_symbols(per_call: Optional[str], server_default: Optional[str]) -> Optional[str]:
    """Combine per-call and server-default symbol paths."""
    if per_call and server_default:
        return f"{per_call};{server_default}"
    return per_call or server_default


def _optional_sections(session, args, timeout: int) -> list[str]:
    """Render the include_* sections shared by the open_* tools."""
    sections = []
    if args.include_stack_trace:
        stack = session.send_command("kb", timeout=timeout)
        sections.append("### Stack Trace\n```\n" + "\n".join(stack) + "\n```\n\n")
    if args.include_modules:
        modules = session.send_command("lm", timeout=timeout)
        sections.append("### Loaded Modules\n```\n" + "\n".join(modules) + "\n```\n\n")
    if args.include_threads:
        threads = session.send_command("~", timeout=timeout)
        sections.append("### Threads\n```\n" + "\n".join(threads) + "\n```\n\n")
    return sections


async def serve(
    cdb_path: Optional[str] = None,
    kd_path: Optional[str] = None,
    symbols_path: Optional[str] = None,
    filter_script: Optional[str] = None,
    timeout: int = 60,
    verbose: bool = False,
    auto_dump_dir_symbols: bool = True,
) -> None:
    """Run the WinDbg MCP server with stdio transport."""
    content_filter = load_filter_script(filter_script) if filter_script else None
    server = _create_server(cdb_path, kd_path, symbols_path, timeout, verbose, content_filter, "stdio", auto_dump_dir_symbols)

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        # raise_exceptions=False (the SDK default) keeps the server alive on a
        # malformed stdin line: the transport forwards the parse error and the
        # message loop logs it instead of crashing the process. See issue #45.
        await server.run(read_stream, write_stream, options)


async def serve_http(  # pragma: no cover - HTTP transport cannot flush coverage on Windows teardown (verified e2e in http_transport.yaml)
    host: str = "127.0.0.1",
    port: int = 8000,
    cdb_path: Optional[str] = None,
    kd_path: Optional[str] = None,
    symbols_path: Optional[str] = None,
    filter_script: Optional[str] = None,
    timeout: int = 60,
    verbose: bool = False,
    auto_dump_dir_symbols: bool = True,
) -> None:
    """Run the WinDbg MCP server with Streamable HTTP transport."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.types import Receive, Scope, Send
    import uvicorn

    content_filter = load_filter_script(filter_script) if filter_script else None
    server = _create_server(cdb_path, kd_path, symbols_path, timeout, verbose, content_filter, "streamable-http", auto_dump_dir_symbols)

    # Create the session manager
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
    )

    # ASGI handler for streamable HTTP connections
    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    app = Starlette(
        debug=verbose,
        routes=[
            Mount("/mcp", app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )

    logger.info(f"Starting MCP WinDbg server with streamable-http transport on {host}:{port}")
    print(f"MCP WinDbg server running on http://{host}:{port}")
    print(f"  MCP endpoint: http://{host}:{port}/mcp")

    config = uvicorn.Config(app, host=host, port=port, log_level="info" if verbose else "warning")
    server_instance = uvicorn.Server(config)
    await server_instance.serve()


def _create_server(
    cdb_path: Optional[str] = None,
    kd_path: Optional[str] = None,
    symbols_path: Optional[str] = None,
    timeout: int = 60,
    verbose: bool = False,
    content_filter: Optional[FilterScript] = None,
    transport: str = "stdio",
    auto_dump_dir_symbols: bool = True,
) -> Server:
    """Create and configure the MCP server with all tools and prompts."""
    server = Server("mcp-windbg")

    def filter_tool_arguments(tool_name: str, arguments: dict | None, call_id: str) -> dict:
        if arguments is None:
            arguments = {}
        if content_filter is None:
            return arguments
        return content_filter.process_input(tool_name, arguments, transport, call_id) or {}

    def filter_tool_content(tool_name: str, content: list[TextContent], call_id: str) -> list[TextContent]:
        if content_filter is None:
            return content
        return content_filter.process_output(tool_name, content, transport, call_id)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_dumps",
                description="""
                List Windows crash dump files in a directory.
                Helps discover dumps to analyze with open_cdb_dump.
                """,
                inputSchema=ListDumps.model_json_schema(),
            ),
            Tool(
                name="open_cdb_dump",
                description="""
                Open and triage a Windows crash dump with cdb.exe (user mode).
                Runs .lastevent and !analyze -v (optionally kb/lm/~) and returns a session_id.
                Use that session_id with run_cdb_command and close_cdb_session.
                """,
                inputSchema=OpenCdbDump.model_json_schema(),
            ),
            Tool(
                name="open_cdb_remote",
                description="""
                Attach to a user-mode remote debug server (-remote) with cdb.exe, e.g. one started
                with 'cdb -server tcp:port=5005 <program>'. Returns a session_id for run_cdb_command
                / send_ctrl_break / close_cdb_session. For kernel targets use open_kd_session instead.
                """,
                inputSchema=OpenCdbRemote.model_json_schema(),
            ),
            Tool(
                name="open_kd_session",
                description="""
                Attach to a kernel target with kd.exe (-k). Waits for the target to connect, breaks in,
                and returns a session_id for run_kd_command / send_ctrl_break / close_kd_session.
                Connection strings: KDNET 'net:port=50000,key=1.2.3.4', named pipe
                'com:pipe,port=\\\\.\\pipe\\com_1,baud=115200,reconnect,resets=0', or serial 'com:port=COM1,baud=115200'.
                """,
                inputSchema=OpenKdSession.model_json_schema(),
            ),
            Tool(
                name="run_cdb_command",
                description="""
                Run a WinDbg/CDB command on a user-mode session (from open_cdb_dump or open_cdb_remote),
                addressed by session_id. Optional timeout_seconds overrides the default.
                """,
                inputSchema=RunCdbCommand.model_json_schema(),
            ),
            Tool(
                name="run_kd_command",
                description="""
                Run a command on a kernel session (from open_kd_session), addressed by session_id.
                Optional timeout_seconds overrides the default (kernel memory reads can be slow).
                """,
                inputSchema=RunKdCommand.model_json_schema(),
            ),
            Tool(
                name="close_cdb_session",
                description="""
                Close a user-mode (cdb) session and release its resources, addressed by session_id.
                """,
                inputSchema=CloseCdbSession.model_json_schema(),
            ),
            Tool(
                name="close_kd_session",
                description="""
                Close a kernel (kd) session and release its resources, addressed by session_id.
                """,
                inputSchema=CloseKdSession.model_json_schema(),
            ),
            Tool(
                name="send_ctrl_break",
                description="""
                Break into a running live session (cdb remote or kd), addressed by session_id.
                Useful to interrupt a running target so commands work again.
                """,
                inputSchema=SendCtrlBreak.model_json_schema(),
            ),
        ]

    @server.call_tool()
    async def call_tool(name, arguments: dict) -> list[TextContent]:
        try:
            call_id = uuid.uuid4().hex
            arguments = filter_tool_arguments(name, arguments, call_id)

            if name == "list_dumps":
                return filter_tool_content(name, _handle_list_dumps(arguments), call_id)

            if name == "open_cdb_dump":
                return filter_tool_content(name, _handle_open_cdb_dump(
                    arguments, cdb_path, symbols_path, timeout, verbose, auto_dump_dir_symbols
                ), call_id)

            if name == "open_cdb_remote":
                return filter_tool_content(name, _handle_open_cdb_remote(
                    arguments, cdb_path, symbols_path, timeout, verbose
                ), call_id)

            if name == "open_kd_session":
                return filter_tool_content(name, _handle_open_kd_session(
                    arguments, kd_path, symbols_path, timeout, verbose
                ), call_id)

            if name == "run_cdb_command":
                return filter_tool_content(name, _handle_run_command(
                    RunCdbCommand(**arguments), "cdb", CDB_COMMAND_TIMEOUT, timeout
                ), call_id)

            if name == "run_kd_command":
                return filter_tool_content(name, _handle_run_command(
                    RunKdCommand(**arguments), "kd", KD_COMMAND_TIMEOUT, timeout
                ), call_id)

            if name == "close_cdb_session":
                return filter_tool_content(name, _handle_close(CloseCdbSession(**arguments).session_id, "cdb"), call_id)

            if name == "close_kd_session":
                close_args = CloseKdSession(**arguments)
                record = _sessions.get(close_args.session_id)
                if record and record["kind"] == "kd":
                    record["session"].resume_on_close = close_args.resume
                return filter_tool_content(name, _handle_close(close_args.session_id, "kd"), call_id)

            if name == "send_ctrl_break":
                return filter_tool_content(name, _handle_send_ctrl_break(SendCtrlBreak(**arguments).session_id), call_id)

            raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Unknown tool: {name}"))

        except McpError:
            raise
        except Exception as e:
            traceback_str = traceback.format_exc()
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Error executing tool {name}: {str(e)}\n{traceback_str}"
            ))

    # -- Tool handlers --------------------------------------------------------

    def _handle_list_dumps(arguments: dict) -> list[TextContent]:
        args = ListDumps(**arguments)
        directory = args.directory_path or get_local_dumps_path()
        if directory is None:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message="No directory path specified and no default dump path found in registry."
            ))
        if not os.path.exists(directory) or not os.path.isdir(directory):
            raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Directory not found: {directory}"))

        pattern = os.path.join(directory, "**", "*.*dmp") if args.recursive else os.path.join(directory, "*.*dmp")
        dump_files = sorted(glob.glob(pattern, recursive=args.recursive))

        if not dump_files:
            return [TextContent(type="text", text=f"No crash dump files (*.*dmp) found in {directory}")]

        text = f"Found {len(dump_files)} crash dump file(s) in {directory}:\n\n"
        for i, dump_file in enumerate(dump_files):
            try:
                size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
            except (OSError, IOError):
                size_mb = "unknown"
            text += f"{i+1}. {dump_file} ({size_mb} MB)\n"
        return [TextContent(type="text", text=text)]

    def _handle_open_cdb_dump(arguments, cdb_path, symbols_path, server_timeout, verbose, auto_dump_dir_symbols):
        # Missing dump_path: help the caller discover dumps (kept from the old tool).
        if not arguments.get("dump_path"):
            return _dump_discovery_help()

        args = OpenCdbDump(**arguments)
        effective = _effective_timeout(args.timeout_seconds, CDB_DUMP_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = CDBSession(
                dump_path=args.dump_path, cdb_path=cdb_path, symbols_path=effective_symbols,
                timeout=effective, verbose=verbose, auto_dump_dir_symbols=auto_dump_dir_symbols,
            )
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to open cdb dump session: {e}"))

        session_id = _register_session(session, "cdb", f"dump {args.dump_path}")
        results = [_session_header(session_id, "cdb", f"crash dump {args.dump_path}")]

        crash_info = session.send_command(".lastevent", timeout=effective)
        results.append("### Crash Information\n```\n" + "\n".join(crash_info) + "\n```\n\n")
        analysis = session.send_command("!analyze -v", timeout=effective)
        results.append("### Crash Analysis\n```\n" + "\n".join(analysis) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_open_cdb_remote(arguments, cdb_path, symbols_path, server_timeout, verbose):
        args = OpenCdbRemote(**arguments)
        effective = _effective_timeout(args.timeout_seconds, CDB_REMOTE_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = CDBSession(
                remote_connection=args.connection_string, cdb_path=cdb_path,
                symbols_path=effective_symbols, timeout=effective, verbose=verbose,
            )
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to open cdb remote session: {e}"))

        session_id = _register_session(session, "cdb", f"remote {args.connection_string}")
        results = [_session_header(session_id, "cdb", f"remote target {args.connection_string}")]

        target_info = session.send_command("!peb", timeout=effective)
        results.append("### Target Process Information\n```\n" + "\n".join(target_info) + "\n```\n\n")
        registers = session.send_command("r", timeout=effective)
        results.append("### Current Registers\n```\n" + "\n".join(registers) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_open_kd_session(arguments, kd_path, symbols_path, server_timeout, verbose):
        args = OpenKdSession(**arguments)
        effective = _effective_timeout(args.timeout_seconds, KD_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = KDSession(
                kernel_connection=args.connection_string, kd_path=kd_path,
                symbols_path=effective_symbols, timeout=effective, verbose=verbose,
            )
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to open kd session: {e}"))

        session_id = _register_session(session, "kd", f"kernel {args.connection_string}")
        results = [_session_header(session_id, "kd", f"kernel target {args.connection_string}")]

        target_info = session.send_command("vertarget", timeout=effective)
        results.append("### Kernel Target Information\n```\n" + "\n".join(target_info) + "\n```\n\n")
        registers = session.send_command("r", timeout=effective)
        results.append("### Current Registers\n```\n" + "\n".join(registers) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_run_command(args, kind, tool_default, server_timeout) -> list[TextContent]:
        session = _require_session(args.session_id, kind)
        effective = _effective_timeout(args.timeout_seconds, tool_default, server_timeout)
        output = session.send_command(args.command, timeout=effective)
        text = f"Command: {args.command}\n\nOutput:\n```\n" + "\n".join(output) + "\n```"
        return [TextContent(type="text", text=text)]

    def _handle_close(session_id, kind) -> list[TextContent]:
        if _close_session(session_id, kind):
            return [TextContent(type="text", text=f"Successfully closed {kind} session {session_id}")]
        return [TextContent(type="text", text=f"No active {kind} session found for session_id {session_id}")]

    def _handle_send_ctrl_break(session_id) -> list[TextContent]:
        record = _sessions.get(session_id)
        if record is None:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown session_id {session_id!r}. Open a session first."
            ))
        session = record["session"]
        if not getattr(session, "is_live_session", False):
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"session_id {session_id!r} is a dump session; there is no running target to break into."
            ))
        session.send_ctrl_break()
        return [TextContent(type="text", text=f"Sent CTRL+BREAK to session {session_id} ({record['label']}).")]

    def _session_header(session_id: str, kind: str, what: str) -> str:
        run_tool = f"run_{kind}_command"
        close_tool = f"close_{kind}_session"
        return (
            f"session_id: {session_id}\n\n"
            f"Opened {kind} session for {what}. Use session_id `{session_id}` with "
            f"{run_tool} and {close_tool}.\n\n"
        )

    def _dump_discovery_help() -> list[TextContent]:
        local_dumps_path = get_local_dumps_path()
        dumps_found_text = ""
        if local_dumps_path:
            dump_files = glob.glob(os.path.join(local_dumps_path, "*.*dmp"))
            if dump_files:
                dumps_found_text = f"\n\nI found {len(dump_files)} crash dump(s) in {local_dumps_path}:\n\n"
                for i, dump_file in enumerate(dump_files[:10]):
                    try:
                        size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
                    except (OSError, IOError):
                        size_mb = "unknown"
                    dumps_found_text += f"{i+1}. {dump_file} ({size_mb} MB)\n"
                if len(dump_files) > 10:
                    dumps_found_text += f"\n... and {len(dump_files) - 10} more dump files.\n"
                dumps_found_text += "\nOpen one by passing its path as dump_path."
        return [TextContent(
            type="text",
            text=(f"Please provide a dump_path to open.{dumps_found_text}\n\n"
                  f"Use the 'list_dumps' tool to discover available crash dumps."),
        )]

    # Prompt constants
    DUMP_TRIAGE_PROMPT_NAME = "dump-triage"
    DUMP_TRIAGE_PROMPT_TITLE = "Crash Dump Triage Analysis"
    DUMP_TRIAGE_PROMPT_DESCRIPTION = "Comprehensive single crash dump analysis with detailed metadata extraction and structured reporting"

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name=DUMP_TRIAGE_PROMPT_NAME,
                title=DUMP_TRIAGE_PROMPT_TITLE,
                description=DUMP_TRIAGE_PROMPT_DESCRIPTION,
                arguments=[
                    PromptArgument(
                        name="dump_path",
                        description="Path to the Windows crash dump file to analyze (optional - will prompt if not provided)",
                        required=False,
                    ),
                ],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if arguments is None:
            arguments = {}

        if name == DUMP_TRIAGE_PROMPT_NAME:
            dump_path = arguments.get("dump_path", "")
            try:
                prompt_content = load_prompt("dump-triage")
            except FileNotFoundError as e:
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Prompt file not found: {e}"
                ))

            if dump_path:
                prompt_text = f"**Dump file to analyze:** {dump_path}\n\n{prompt_content}"
            else:
                prompt_text = prompt_content

            return GetPromptResult(
                description=DUMP_TRIAGE_PROMPT_DESCRIPTION,
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=prompt_text
                        ),
                    ),
                ],
            )

        else:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown prompt: {name}"
            ))

    return server


# Clean up function to ensure all sessions are closed when the server exits
def cleanup_sessions():  # pragma: no cover - atexit handler, runs after coverage stops
    """Close all active sessions."""
    for record in _sessions.values():
        try:
            session = record.get("session")
            if session is not None:
                session.shutdown()
        except Exception:
            pass
    _sessions.clear()


# Register cleanup on module exit
import atexit
atexit.register(cleanup_sessions)
