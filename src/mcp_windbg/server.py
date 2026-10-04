import os
import functools
import traceback
import glob
import itertools
import logging
import threading
import uuid
from typing import Dict, List, Optional
from contextlib import ExitStack, asynccontextmanager

from .cdb_session import CDBSession
from .debug_session import DebuggerError
from .kd_session import KDSession
from .filter_script import FilterScript, load_filter_script
from .error_log import log_error

from mcp.shared.exceptions import MCPError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    TextContent,
    CallToolResult,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
import anyio.lowlevel
import anyio.to_thread
from .server_support import (
    CDB_COMMAND_TIMEOUT, CDB_DUMP_OPEN_TIMEOUT, CDB_REMOTE_OPEN_TIMEOUT,
    KD_COMMAND_TIMEOUT, KD_DUMP_OPEN_TIMEOUT, KD_OPEN_TIMEOUT, PROMPT_SPECS,
    WAIT_FOR_BREAK_TIMEOUT, CloseCdbSession, CloseKdSession, ListDumps,
    OpenCdbDump, OpenCdbRemote, OpenKdDump, OpenKdSession, RunCdbCommand,
    RunKdCommand, SendCtrlBreak, WaitForBreak, _combine_symbols,
    _effective_timeout, _init_sections, _is_kernel_dump, _optional_sections,
    get_local_dumps_path, on_get_prompt, on_list_prompts, on_list_tools,
)

logger = logging.getLogger(__name__)

# --- Session registry --------------------------------------------------------
# Every open_* tool creates a session and returns an opaque session_id; every
# other tool addresses a session by that id. A record tracks the live session
# object, its kind ("cdb" or "kd"), and a human label for messages.
_sessions: Dict[str, dict] = {}
_session_lock = threading.Lock()
_closing_sessions: set[str] = set()


def _new_session_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:8]}"


def _register_session(session, kind: str, label: str, cleanup: ExitStack) -> str:
    session_id = _new_session_id(kind)
    with _session_lock:
        _sessions[session_id] = {"session": session, "kind": kind, "label": label}
    cleanup.callback(_close_session, session_id, kind)
    return session_id


def _require_session(session_id: str, kind: str):
    """Return the session for ``session_id``, or raise a helpful MCPError.

    Enforces that the session is of the expected kind so ``run_cdb_command`` on a
    kernel session (or vice versa) fails clearly instead of misbehaving.
    """
    record = _sessions.get(session_id)
    if record is None:
        raise MCPError(INVALID_PARAMS, (
                f"Unknown session_id {session_id!r}. Open a session first - the "
                f"open_* tools return a session_id to use here."
            ))
    if record["kind"] != kind:
        actual = record["kind"]
        raise MCPError(INVALID_PARAMS, (
                f"session_id {session_id!r} is a {actual} session, not {kind}. "
                f"Use run_{actual}_command / close_{actual}_session for it."
            ))
    return record["session"]


def _require_live_session(session_id: str, what: str):
    """Return the record for ``session_id``, requiring a live (non-dump) target.

    Shared by the tools that only make sense against something that runs -
    ``send_ctrl_break`` and ``wait_for_break`` - and accepts either kind, since a
    cdb remote and a kd session both have a target to stop.
    """
    record = _sessions.get(session_id)
    if record is None:
        raise MCPError(INVALID_PARAMS, f"Unknown session_id {session_id!r}. Open a session first.")
    session = record["session"]
    if not getattr(session, "is_live_session", False):
        raise MCPError(INVALID_PARAMS, (
                f"session_id {session_id!r} is a dump session; there is no "
                f"running target to {what}."
            ))
    return record


def _close_session(session_id: str, kind: str, resume: Optional[bool] = None) -> bool:
    """Shut down and forget a session; returns False if id/kind did not match."""
    with _session_lock:
        record = _sessions.get(session_id)
        if record is None or record["kind"] != kind or session_id in _closing_sessions:
            return False
        _closing_sessions.add(session_id)
    try:
        if resume is not None:
            record["session"].resume_on_close = resume
        record["session"].shutdown()
        with _session_lock:
            _sessions.pop(session_id, None)
        return True
    except Exception as error:
        log_error("Close failed for %s; session retained", session_id, exc_info=True)
        raise DebuggerError(f"Could not close {session_id}; retry close_{kind}_session: {error}") from error
    finally:
        with _session_lock:
            _closing_sessions.discard(session_id)


def _build_session(cls, kind, label, cleanup, **settings):
    """Keep a failed constructor retryable if it could not reap its process."""
    session = cls.__new__(cls)
    try:
        cls.__init__(session, **settings)
    except Exception as error:
        if getattr(session, "process", None) is not None:
            recovery_id = _register_session(session, kind, label, cleanup)
            raise DebuggerError(
                f"Startup failed: {error}; cleanup incomplete. "
                f"Retry close_{kind}_session with session_id={recovery_id}"
            ) from error
        raise
    return session


async def serve(
    cdb_path: Optional[str] = None,
    kd_path: Optional[str] = None,
    symbols_path: Optional[str] = None,
    filter_script: Optional[str] = None,
    timeout: int = 60,
    verbose: bool = False,
    auto_dump_dir_symbols: bool = True,
    init_commands: Optional[List[str]] = None,
    kernel_init_commands: Optional[List[str]] = None,
) -> None:
    """Run the WinDbg MCP server with stdio transport."""
    content_filter = load_filter_script(filter_script) if filter_script else None
    server = _create_server(
        cdb_path, kd_path, symbols_path, timeout, verbose, content_filter, "stdio",
        auto_dump_dir_symbols, init_commands, kernel_init_commands,
    )

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
    init_commands: Optional[List[str]] = None,
    kernel_init_commands: Optional[List[str]] = None,
) -> None:
    """Run the WinDbg MCP server with Streamable HTTP transport."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.types import Receive, Scope, Send
    import uvicorn

    content_filter = load_filter_script(filter_script) if filter_script else None
    server = _create_server(
        cdb_path, kd_path, symbols_path, timeout, verbose, content_filter, "streamable-http",
        auto_dump_dir_symbols, init_commands, kernel_init_commands,
    )

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
    init_commands: Optional[List[str]] = None,
    kernel_init_commands: Optional[List[str]] = None,
) -> Server:
    """Create and configure the MCP server with all tools and prompts.

    mcp 2.x takes its handlers as constructor arguments rather than through
    ``@server.<method>()`` decorators, so the server itself is built at the end
    of this function, once the handlers below exist.
    """

    def _init_for(kernel: bool) -> list[str]:
        """Init commands for a new session: the shared ones, then the kernel-only ones."""
        return list(init_commands or []) + (list(kernel_init_commands or []) if kernel else [])

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


    async def on_call_tool(ctx, params) -> CallToolResult:
        """Dispatch a tool call and wrap its content in the 2.x result type.

        A raised MCPError still reaches the caller as an error - the SDK turns
        it into a JSON-RPC error response - so the handlers below keep raising
        rather than hand-building error results.
        """
        content = await _dispatch_tool(params.name, params.arguments or {})
        return CallToolResult(content=content)

    async def _run_debugger_handler(handler, *args):
        # Debugger startup, commands, and shutdown can wait for seconds or minutes.
        # Keep the event loop available for other tools, especially break-in.
        return await anyio.to_thread.run_sync(functools.partial(handler, *args))

    cleanup_limiter = anyio.CapacityLimiter(2)
    discovery_limiter = anyio.CapacityLimiter(2)

    async def _run_cleanup_handler(handler, *args):
        # Cleanup must remain available when all normal workers wait for targets.
        return await anyio.to_thread.run_sync(functools.partial(handler, *args), limiter=cleanup_limiter)

    async def _run_open_handler(handler, name, arguments, call_id, *settings):
        # An open owns its debugger until triage AND output filtering succeed.
        # Roll back only this call's session; unrelated sessions may be busy.
        cleanup = ExitStack()
        failure = None
        try:
            content = await _run_debugger_handler(handler, arguments, *settings, cleanup)
            result = filter_tool_content(name, content, call_id)
            await anyio.lowlevel.checkpoint_if_cancelled()
            cleanup.pop_all()
            return result
        except BaseException as error:
            failure = error
            raise
        finally:
            # Keep hooks on the event loop, but never block it on shutdown.
            # Cancellation must not skip the rollback of an undisclosed open.
            with anyio.CancelScope(shield=True):
                try:
                    await _run_cleanup_handler(cleanup.close)
                except DebuggerError as cleanup_error:
                    if isinstance(failure, Exception):
                        raise DebuggerError(f"{failure}; cleanup incomplete: {cleanup_error}") from failure
                    if failure is None:
                        raise
                    # Preserve cancellation; the retained recovery id is logged.

    async def _dispatch_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            call_id = uuid.uuid4().hex
            arguments = filter_tool_arguments(name, arguments, call_id)

            if name == "list_dumps":
                content = await anyio.to_thread.run_sync(
                    functools.partial(_handle_list_dumps, arguments), limiter=discovery_limiter,
                )
                return filter_tool_content(name, content, call_id)

            if name == "open_cdb_dump":
                return await _run_open_handler(_handle_open_cdb_dump,
                    name, arguments, call_id, cdb_path, symbols_path, timeout, verbose, auto_dump_dir_symbols
                )

            if name == "open_cdb_remote":
                return await _run_open_handler(_handle_open_cdb_remote,
                    name, arguments, call_id, cdb_path, symbols_path, timeout, verbose
                )

            if name == "open_kd_session":
                return await _run_open_handler(_handle_open_kd_session,
                    name, arguments, call_id, kd_path, symbols_path, timeout, verbose
                )

            if name == "open_kd_dump":
                return await _run_open_handler(_handle_open_kd_dump,
                    name, arguments, call_id, kd_path, symbols_path, timeout, verbose, auto_dump_dir_symbols
                )

            if name == "run_cdb_command":
                return filter_tool_content(name, await _run_debugger_handler(_handle_run_command,
                    RunCdbCommand(**arguments), "cdb", CDB_COMMAND_TIMEOUT, timeout
                ), call_id)

            if name == "run_kd_command":
                return filter_tool_content(name, await _run_debugger_handler(_handle_run_command,
                    RunKdCommand(**arguments), "kd", KD_COMMAND_TIMEOUT, timeout
                ), call_id)

            if name == "close_cdb_session":
                return filter_tool_content(name, await _run_cleanup_handler(
                    _handle_close, CloseCdbSession(**arguments).session_id, "cdb"
                ), call_id)

            if name == "close_kd_session":
                close_args = CloseKdSession(**arguments)
                return filter_tool_content(name, await _run_cleanup_handler(
                    _handle_close, close_args.session_id, "kd", close_args.resume
                ), call_id)

            if name == "send_ctrl_break":
                # This escape hatch must not queue behind blocked debugger workers.
                return filter_tool_content(name, _handle_send_ctrl_break(SendCtrlBreak(**arguments).session_id), call_id)

            if name == "wait_for_break":
                return filter_tool_content(name, await _handle_wait_for_break(WaitForBreak(**arguments)), call_id)

            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")

        except MCPError:
            log_error("MCP tool %s failed", name, exc_info=True)
            raise
        except DebuggerError as e:
            log_error("Debugger tool %s failed", name, exc_info=True)
            # An expected debugger failure: its message is the whole story, and a
            # stack trace would repeat it (partial output included) a second time.
            raise MCPError(INTERNAL_ERROR, f"Error executing tool {name}: {e}")
        except Exception as e:
            log_error("Unexpected tool %s failure", name, exc_info=True)
            traceback_str = traceback.format_exc()
            raise MCPError(INTERNAL_ERROR, f"Error executing tool {name}: {str(e)}\n{traceback_str}")

    # -- Tool handlers --------------------------------------------------------

    def _handle_list_dumps(arguments: dict) -> list[TextContent]:
        args = ListDumps(**arguments)
        directory = args.directory_path or get_local_dumps_path()
        if directory is None:
            raise MCPError(INVALID_PARAMS, "No directory path specified and no default dump path found in registry.")
        if not os.path.exists(directory) or not os.path.isdir(directory):
            raise MCPError(INVALID_PARAMS, f"Directory not found: {directory}")

        pattern = os.path.join(directory, "**", "*.*dmp") if args.recursive else os.path.join(directory, "*.*dmp")
        # ponytail: filesystem-order offset pages avoid retaining/sorting a whole
        # directory tree; use a persistent index if stable snapshots are needed.
        page = list(itertools.islice(glob.iglob(pattern, recursive=args.recursive), args.offset, args.offset + args.limit + 1))
        has_more = len(page) > args.limit
        dump_files = page[:args.limit]

        if not dump_files:
            return [TextContent(type="text", text=f"No crash dump files (*.*dmp) found in {directory}")]

        text = f"Found {len(dump_files)} crash dump file(s) on this page in {directory} (offset {args.offset}):\n\n"
        for i, dump_file in enumerate(dump_files, start=args.offset):
            try:
                size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
            except (OSError, IOError):
                size_mb = "unknown"
            text += f"{i+1}. {dump_file} ({size_mb} MB)\n"
        if has_more:
            text += f"\nMore dumps available: call list_dumps with offset={args.offset + args.limit}, limit={args.limit}.\n"
        return [TextContent(type="text", text=text)]

    def _handle_open_cdb_dump(arguments, cdb_path, symbols_path, server_timeout, verbose, auto_dump_dir_symbols, cleanup):
        # Missing dump_path: help the caller discover dumps (kept from the old tool).
        if not arguments.get("dump_path"):
            return _dump_discovery_help()

        args = OpenCdbDump(**arguments)
        effective = _effective_timeout(args.timeout_seconds, CDB_DUMP_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = _build_session(CDBSession, "cdb", f"dump {args.dump_path}", cleanup,
                dump_path=args.dump_path, cdb_path=cdb_path, symbols_path=effective_symbols,
                timeout=effective, verbose=verbose, auto_dump_dir_symbols=auto_dump_dir_symbols,
            )
        except Exception as e:
            raise MCPError(INTERNAL_ERROR, f"Failed to open cdb dump session: {e}")

        session_id = _register_session(session, "cdb", f"dump {args.dump_path}", cleanup)
        results = [_session_header(session_id, "cdb", f"crash dump {args.dump_path}")]
        results.extend(_init_sections(session, _init_for(_is_kernel_dump(args.dump_path)), effective))

        crash_info = session.send_command(".lastevent", timeout=effective)
        results.append("### Crash Information\n```\n" + "\n".join(crash_info) + "\n```\n\n")
        analysis = session.send_command("!analyze -v", timeout=effective)
        results.append("### Crash Analysis\n```\n" + "\n".join(analysis) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_open_cdb_remote(arguments, cdb_path, symbols_path, server_timeout, verbose, cleanup):
        args = OpenCdbRemote(**arguments)
        effective = _effective_timeout(args.timeout_seconds, CDB_REMOTE_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = _build_session(CDBSession, "cdb", f"remote {args.connection_string}", cleanup,
                remote_connection=args.connection_string, cdb_path=cdb_path,
                symbols_path=effective_symbols, timeout=effective, verbose=verbose,
            )
        except Exception as e:
            raise MCPError(INTERNAL_ERROR, f"Failed to open cdb remote session: {e}")

        session_id = _register_session(session, "cdb", f"remote {args.connection_string}", cleanup)
        results = [_session_header(session_id, "cdb", f"remote target {args.connection_string}")]
        results.extend(_init_sections(session, _init_for(kernel=False), effective))

        target_info = session.send_command("!peb", timeout=effective)
        results.append("### Target Process Information\n```\n" + "\n".join(target_info) + "\n```\n\n")
        registers = session.send_command("r", timeout=effective)
        results.append("### Current Registers\n```\n" + "\n".join(registers) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_open_kd_session(arguments, kd_path, symbols_path, server_timeout, verbose, cleanup):
        args = OpenKdSession(**arguments)
        effective = _effective_timeout(args.timeout_seconds, KD_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = _build_session(KDSession, "kd", f"kernel {args.connection_string}", cleanup,
                kernel_connection=args.connection_string, kd_path=kd_path,
                symbols_path=effective_symbols, timeout=effective, verbose=verbose,
            )
        except Exception as e:
            raise MCPError(INTERNAL_ERROR, f"Failed to open kd session: {e}")

        session_id = _register_session(session, "kd", f"kernel {args.connection_string}", cleanup)
        results = [_session_header(session_id, "kd", f"kernel target {args.connection_string}")]
        results.extend(_init_sections(session, _init_for(kernel=True), effective))

        target_info = session.send_command("vertarget", timeout=effective)
        results.append("### Kernel Target Information\n```\n" + "\n".join(target_info) + "\n```\n\n")
        registers = session.send_command("r", timeout=effective)
        results.append("### Current Registers\n```\n" + "\n".join(registers) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_open_kd_dump(arguments, kd_path, symbols_path, server_timeout, verbose, auto_dump_dir_symbols, cleanup):
        args = OpenKdDump(**arguments)
        effective = _effective_timeout(args.timeout_seconds, KD_DUMP_OPEN_TIMEOUT, server_timeout)
        effective_symbols = _combine_symbols(args.symbols_path, symbols_path)
        try:
            session = _build_session(KDSession, "kd", f"dump {args.dump_path}", cleanup,
                dump_path=args.dump_path, kd_path=kd_path, symbols_path=effective_symbols,
                timeout=effective, verbose=verbose, auto_dump_dir_symbols=auto_dump_dir_symbols,
            )
        except Exception as e:
            raise MCPError(INTERNAL_ERROR, f"Failed to open kd dump session: {e}")

        session_id = _register_session(session, "kd", f"kernel dump {args.dump_path}", cleanup)
        results = [_session_header(session_id, "kd", f"kernel dump {args.dump_path}")]
        results.extend(_init_sections(session, _init_for(_is_kernel_dump(args.dump_path)), effective))

        target_info = session.send_command("vertarget", timeout=effective)
        results.append("### Kernel Target Information\n```\n" + "\n".join(target_info) + "\n```\n\n")
        analysis = session.send_command("!analyze -v", timeout=effective)
        results.append("### Crash Analysis\n```\n" + "\n".join(analysis) + "\n```\n\n")
        results.extend(_optional_sections(session, args, effective))
        return [TextContent(type="text", text="".join(results))]

    def _handle_run_command(args, kind, tool_default, server_timeout) -> list[TextContent]:
        session = _require_session(args.session_id, kind)
        effective = _effective_timeout(args.timeout_seconds, tool_default, server_timeout)
        output = session.send_command(args.command, timeout=effective)
        text = f"Command: {args.command}\n\nOutput:\n```\n" + "\n".join(output) + "\n```"
        return [TextContent(type="text", text=text)]

    def _handle_close(session_id, kind, resume=None) -> list[TextContent]:
        if _close_session(session_id, kind, resume):
            return [TextContent(type="text", text=f"Successfully closed {kind} session {session_id}")]
        return [TextContent(type="text", text=f"No active {kind} session found for session_id {session_id}")]

    def _handle_send_ctrl_break(session_id) -> list[TextContent]:
        record = _require_live_session(session_id, "break into")
        label = record["label"]
        record["session"].send_ctrl_break()
        return [TextContent(type="text", text=f"Sent CTRL+BREAK to session {session_id} ({label}).")]

    async def _handle_wait_for_break(args: WaitForBreak) -> list[TextContent]:
        session = _require_live_session(args.session_id, "wait on")["session"]
        # Deliberately not _effective_timeout: the --timeout floor is about how
        # long a *command* may take, and has nothing to say about how long you
        # are willing to sit on a target waiting for it to bugcheck.
        wait = (
            args.timeout_seconds
            if args.timeout_seconds and args.timeout_seconds > 0
            else WAIT_FOR_BREAK_TIMEOUT
        )
        # This blocks for minutes by design. Run it on a worker thread so the
        # event loop keeps serving - otherwise the server could not answer the
        # send_ctrl_break this tool's own timeout message tells you to use.
        output = await anyio.to_thread.run_sync(
            functools.partial(session.wait_for_break, timeout=wait)
        )
        if not output:
            return [TextContent(type="text", text="Target stopped, printing nothing.")]
        return [TextContent(
            type="text",
            text="Target stopped.\n\nOutput:\n```\n" + "\n".join(output) + "\n```",
        )]

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

    return Server(
        "mcp-windbg",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_prompts=on_list_prompts,
        on_get_prompt=on_get_prompt,
    )


# Clean up function to ensure all sessions are closed when the server exits
def cleanup_sessions():  # pragma: no cover - atexit handler, runs after coverage stops
    """Close all active sessions."""
    with _session_lock:
        records = list(_sessions.items())
    for session_id, record in records:
        try:
            _close_session(session_id, record["kind"])
        except DebuggerError:
            pass  # Already logged; ownership remains available for another retry.


# Register cleanup on module exit
import atexit
atexit.register(cleanup_sessions)
