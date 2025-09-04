import os
import traceback
import glob
import winreg
from typing import Dict, Optional

from .cdb_session import CDBSession, CDBError
from .windbg_session import WinDbgSession

from mcp.shared.exceptions import McpError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    ErrorData,
    TextContent,
    Tool,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from pydantic import BaseModel, Field, model_validator

# Dictionary to store CDB sessions keyed by dump file path
active_sessions: Dict[str, CDBSession] = {}

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

class OpenWindbgDump(BaseModel):
    """Parameters for analyzing a crash dump."""
    dump_path: str = Field(description="Path to the Windows crash dump file")
    include_stack_trace: bool = Field(description="Whether to include stack traces in the analysis")
    include_modules: bool = Field(description="Whether to include loaded module information")
    include_threads: bool = Field(description="Whether to include thread information")


class OpenWindbgRemote(BaseModel):
    """Parameters for connecting to a remote debug session."""
    connection_string: str = Field(description="Remote connection string (e.g., 'tcp:Port=5005,Server=192.168.0.100')")
    include_stack_trace: bool = Field(default=False, description="Whether to include stack traces in the analysis")
    include_modules: bool = Field(default=False, description="Whether to include loaded module information")
    include_threads: bool = Field(default=False, description="Whether to include thread information")


class AttachWindbgParams(BaseModel):
    """Attach to an already-running WinDbg instance that has
    windbg_mcp_plugin.js loaded and initialised with mcp_init.
    """
    timeout: int | None = Field(
        default=None,
        description="Optional timeout (in seconds) to wait for the attach handshake."
    )
    verbose: bool = Field(
        default=False,
        description="Emit verbose logging during the attach process."
    )


class RunWindbgCmdParams(BaseModel):
    """Parameters for executing a WinDBG command."""
    dump_path: Optional[str] = Field(
        default=None,
        description=("Path to the dump file *when talking to CDB*.  "
                     "Omit this when you have previously called `attach_windbg`.")
    )
    connection_string: Optional[str] = Field(default=None, description="Remote connection string (e.g., 'tcp:Port=5005,Server=192.168.0.100')")
    command: str = Field(description="WinDBG command to execute")

    @model_validator(mode='after')
    def validate_connection_params(self):
        """Validate that exactly one of dump_path or connection_string is provided."""
        if not self.dump_path and not self.connection_string:
            raise ValueError("Either dump_path or connection_string must be provided")
        if self.dump_path and self.connection_string:
            raise ValueError("dump_path and connection_string are mutually exclusive")
        return self


class CloseWindbgDumpParams(BaseModel):
    """Parameters for unloading a crash dump."""
    dump_path: str = Field(description="Path to the Windows crash dump file to unload")


class CloseWindbgRemoteParams(BaseModel):
    """Parameters for closing a remote debugging connection."""
    connection_string: str = Field(description="Remote connection string to close")

class CloseWindbgAttachParams(BaseModel):
    """Parameters for disconnecting from an attached WinDbg (.js) session."""
    # intentionally empty – we close the single attached session keyed by __windbg_attach__
    pass

class ListWindbgDumpsParams(BaseModel):
    """Parameters for listing crash dumps in a directory."""
    directory_path: Optional[str] = Field(
        default=None,
        description="Directory path to search for dump files. If not specified, will use the configured dump path from registry."
    )
    recursive: bool = Field(
        default=False,
        description="Whether to search recursively in subdirectories"
    )


def get_or_create_session(
    dump_path: Optional[str] = None,
    connection_string: Optional[str] = None,
    cdb_path: Optional[str] = None,
    symbols_path: Optional[str] = None,
    timeout: int = 30,
    verbose: bool = False
) -> CDBSession:
    """Get an existing CDB session or create a new one."""
    if not dump_path and not connection_string:
        raise ValueError("Either dump_path or connection_string must be provided")
    if dump_path and connection_string:
        raise ValueError("dump_path and connection_string are mutually exclusive")
    
    # Create session identifier
    if dump_path:
        session_id = os.path.abspath(dump_path)
    else:
        session_id = f"remote:{connection_string}"

    if session_id not in active_sessions or active_sessions[session_id] is None:
        try:
            session = CDBSession(
                dump_path=dump_path,
                remote_connection=connection_string,
                cdb_path=cdb_path,
                symbols_path=symbols_path,
                timeout=timeout,
                verbose=verbose
            )
            active_sessions[session_id] = session
            return session
        except Exception as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Failed to create CDB session: {str(e)}"
            ))
    
    return active_sessions[session_id]


def unload_session(dump_path: Optional[str] = None,
                   connection_string: Optional[str] = None,
                   is_windbg_attach: Optional[bool] = None) -> bool:
    """Unload and clean up a CDB session."""
    if not dump_path and not connection_string and not is_windbg_attach:
        return False
    if sum(x is not None for x in (dump_path, connection_string, is_windbg_attach)) > 1:
        return False
    
    # Create session identifier
    if dump_path:
        session_id = os.path.abspath(dump_path)
    elif connection_string:
        session_id = f"remote:{connection_string}"
    else:
        session_id = "__windbg_attach__"

    if session_id in active_sessions and active_sessions[session_id] is not None:
        try:
            active_sessions[session_id].shutdown()
            del active_sessions[session_id]
            return True
        except Exception:
            return False
    
    return False


def execute_common_analysis_commands(session: CDBSession) -> dict:
    """
    Execute common analysis commands and return the results.
    
    Returns a dictionary with the results of various analysis commands.
    """
    results = {}
    
    try:
        results["info"] = session.send_command(".lastevent")
        results["exception"] = session.send_command("!analyze -v")
        results["modules"] = session.send_command("lm")
        results["threads"] = session.send_command("~")
    except CDBError as e:
        results["error"] = str(e)
    
    return results


# -----------------------------------------------------------------
# Attach to a running WinDbg-JS session
# -----------------------------------------------------------------
def handle_attach_windbg(
    arguments: dict,
    *,
    timeout: int,
    verbose: bool,
) -> list[TextContent]:
    """
    Attach to an existing WinDbg instance that has `windbg_mcp_plugin.js`
    loaded and initialised with `mcp_init`.

    The JS plug-in drops a temp JSON file (windbg_mcp_<id>.json) that the
    Python side polls for; creating a WinDbgSession with dump_path=None
    triggers that handshake.
    """
    # WinDbg support may be optional – fail gracefully if the import failed
    if WinDbgSession is None:                                   # set in the top-level try/except
        raise McpError(ErrorData(
            code=INTERNAL_ERROR,
            message="WinDbg support is not available (WinDbgSession import failed)"
        ))

    # Parse optional params (timeout / verbose) – both are optional
    params = AttachWindbgParams(**arguments)

    # Always use the same key in the registry so we re-use the session
    key = "__windbg_attach__"

    try:
        if key not in active_sessions or active_sessions[key] is None:
            # Create a *new* attach-mode session (dump_path=None)
            session = WinDbgSession(
                dump_path=None,
                timeout=params.timeout or timeout,
                verbose=params.verbose or verbose,
            )
            active_sessions[key] = session
        else:
            session = active_sessions[key]

        banner = (
            f"[V] Attached to WinDbg session **{getattr(session, 'session_id', 'unknown')}**\n\n"
            f"You can now use `run_windbg_cmd` to execute debugger commands."
        )
        return [TextContent(type="text", text=banner)]

    except Exception as e:                                      # pragma: no cover
        raise McpError(ErrorData(
            code=INTERNAL_ERROR,
            message=f"Failed to attach to WinDbg: {e}"
        ))

async def serve(
    cdb_path: Optional[str] = None,
    symbols_path: Optional[str] = None,
    timeout: int = 30,
    verbose: bool = False,
) -> None:
    """Run the WinDBG MCP server.

    Args:
        cdb_path: Optional custom path to cdb.exe
        symbols_path: Optional custom symbols path
        timeout: Command timeout in seconds
        verbose: Whether to enable verbose output
    """
    server = Server("mcp-windbg")
    
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="open_windbg_dump",
                description="""
                Analyze a Windows crash dump file using WinDBG/CDB.
                This tool executes common WinDBG commands to analyze the crash dump and returns the results.
                """,
                inputSchema=OpenWindbgDump.model_json_schema(),
            ),
            Tool(
                name="open_windbg_remote",
                description="""
                Connect to a remote debugging session using WinDBG/CDB.
                This tool establishes a remote debugging connection and allows you to analyze the target process.
                """,
                inputSchema=OpenWindbgRemote.model_json_schema(),
            ),
            Tool(
                name="attach_windbg",
                description="""
                Attach to a live WinDbg session that has 'windbg_mcp_plugin.js' loaded
                and initialised (via mcp_init).  Use this when you already have WinDbg
                open with a dump, TTD trace, or live target and simply want the MCP
                server to talk to that session instead of launching CDB.
                """,
                inputSchema=AttachWindbgParams.model_json_schema(),
            ),
            Tool(
                name="run_windbg_cmd",
                description="""
                Execute a specific WinDBG command on a loaded crash dump, remote cdb session or remote session attached with .js script.
                This tool allows you to run any WinDBG command and get the output.
                """,
                inputSchema=RunWindbgCmdParams.model_json_schema(),
            ),
            Tool(
                name="close_windbg_dump",
                description="""
                Unload a crash dump and release resources.
                Use this tool when you're done analyzing a crash dump to free up resources.
                """,
                inputSchema=CloseWindbgDumpParams.model_json_schema(),
            ),
            Tool(
                name="close_windbg_remote",
                description="""
                Close a remote debugging connection and release resources.
                Use this tool when you're done with a remote debugging session to free up resources.
                """,
                inputSchema=CloseWindbgRemoteParams.model_json_schema(),
            ),
            Tool(
                name="close_windbg_attach",
                description="""
                Disconnect from a live WinDbg session previously attached with 'attach_windbg'.
                This signals the JS plugin to shut down (via a _shutdown.json) and clears
                the server-side session cache.
                """,
                inputSchema=CloseWindbgAttachParams.model_json_schema(),
            ),
            Tool(
                name="list_windbg_dumps",
                description="""
                List Windows crash dump files in the specified directory.
                This tool helps you discover available crash dumps that can be analyzed.
                """,
                inputSchema=ListWindbgDumpsParams.model_json_schema(),
            )
        ]

    @server.call_tool()
    async def call_tool(name, arguments: dict) -> list[TextContent]:
        try:
            if name == "open_windbg_dump":
                # Check if dump_path is missing or empty
                if "dump_path" not in arguments or not arguments.get("dump_path"):
                    local_dumps_path = get_local_dumps_path()
                    dumps_found_text = ""
                    
                    if local_dumps_path:
                        # Find dump files in the local dumps directory
                        search_pattern = os.path.join(local_dumps_path, "*.*dmp")
                        dump_files = glob.glob(search_pattern)
                        
                        if dump_files:
                            dumps_found_text = f"\n\nI found {len(dump_files)} crash dump(s) in {local_dumps_path}:\n\n"
                            for i, dump_file in enumerate(dump_files[:10]):  # Limit to 10 dumps to avoid clutter
                                try:
                                    size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
                                except (OSError, IOError):
                                    size_mb = "unknown"
                                
                                dumps_found_text += f"{i+1}. {dump_file} ({size_mb} MB)\n"
                                
                            if len(dump_files) > 10:
                                dumps_found_text += f"\n... and {len(dump_files) - 10} more dump files.\n"
                                
                            dumps_found_text += "\nYou can analyze one of these dumps by specifying its path."
                    
                    return [TextContent(
                        type="text",
                        text=f"Please provide a path to a crash dump file to analyze.{dumps_found_text}\n\n"
                              f"You can use the 'list_windbg_dumps' tool to discover available crash dumps."
                    )]
                
                args = OpenWindbgDump(**arguments)
                session = get_or_create_session(
                    dump_path=args.dump_path, cdb_path=cdb_path, symbols_path=symbols_path, timeout=timeout, verbose=verbose
                )
                
                results = []
                
                crash_info = session.send_command(".lastevent")
                results.append("### Crash Information\n```\n" + "\n".join(crash_info) + "\n```\n\n")
                
                # Run !analyze -v
                analysis = session.send_command("!analyze -v")
                results.append("### Crash Analysis\n```\n" + "\n".join(analysis) + "\n```\n\n")
                
                # Optional
                if args.include_stack_trace:
                    stack = session.send_command("kb")
                    results.append("### Stack Trace\n```\n" + "\n".join(stack) + "\n```\n\n")
                
                if args.include_modules:
                    modules = session.send_command("lm")
                    results.append("### Loaded Modules\n```\n" + "\n".join(modules) + "\n```\n\n")
                
                if args.include_threads:
                    threads = session.send_command("~")
                    results.append("### Threads\n```\n" + "\n".join(threads) + "\n```\n\n")
                
                return [TextContent(type="text", text="".join(results))]

            elif name == "open_windbg_remote":
                args = OpenWindbgRemote(**arguments)
                session = get_or_create_session(
                    connection_string=args.connection_string, cdb_path=cdb_path, symbols_path=symbols_path, timeout=timeout, verbose=verbose
                )

                results = []

                # Get target information for remote debugging
                target_info = session.send_command("!peb")
                results.append("### Target Process Information\n```\n" + "\n".join(target_info) + "\n```\n\n")

                # Get current state
                current_state = session.send_command("r")
                results.append("### Current Registers\n```\n" + "\n".join(current_state) + "\n```\n\n")

                # Optional
                if args.include_stack_trace:
                    stack = session.send_command("kb")
                    results.append("### Stack Trace\n```\n" + "\n".join(stack) + "\n```\n\n")

                if args.include_modules:
                    modules = session.send_command("lm")
                    results.append("### Loaded Modules\n```\n" + "\n".join(modules) + "\n```\n\n")

                if args.include_threads:
                    threads = session.send_command("~")
                    results.append("### Threads\n```\n" + "\n".join(threads) + "\n```\n\n")

                return [TextContent(
                    type="text",
                    text="".join(results)
                )]

            elif name == "attach_windbg":
                return handle_attach_windbg(
                    arguments,
                    timeout=timeout,
                    verbose=verbose,
                )

            elif name == "run_windbg_cmd":
                args = RunWindbgCmdParams(**arguments)
                # ── decide which engine we target ─────────────────────────────
                if args.dump_path or args.connection_string:
                    # Classic CDB flow (keyed by dump path)
                    session = get_or_create_session(
                        dump_path=args.dump_path, connection_string=args.connection_string,
                        cdb_path=cdb_path, symbols_path=symbols_path, timeout=timeout, verbose=verbose
                    )
                else:
                    # No dump_path -> expect exactly one attached WinDbg session
                    key = "__windbg_attach__"
                    session = active_sessions.get(key)
                    if session is None:
                        raise McpError(ErrorData(
                            code=INVALID_PARAMS,
                            message=("No dump_path provided and no active WinDbg "
                                     "attachment found.  Call `attach_windbg` first "
                                     "or supply a dump_path.")
                        ))
                output = session.send_command(args.command)
                
                return [TextContent(
                    type="text",
                    text=f"Command: {args.command}\n\nOutput:\n```\n" + "\n".join(output) + "\n```"
                )]
                
            elif name == "close_windbg_dump":
                args = CloseWindbgDumpParams(**arguments)
                success = unload_session(dump_path=args.dump_path)
                if success:
                    return [TextContent(
                        type="text",
                        text=f"Successfully unloaded crash dump: {args.dump_path}"
                    )]
                else:
                    return [TextContent(
                        type="text",
                        text=f"No active session found for crash dump: {args.dump_path}"
                    )]

            elif name == "close_windbg_remote":
                args = CloseWindbgRemoteParams(**arguments)
                success = unload_session(connection_string=args.connection_string)
                if success:
                    return [TextContent(
                        type="text",
                        text=f"Successfully closed remote connection: {args.connection_string}"
                    )]
                else:
                    return [TextContent(
                        type="text",
                        text=f"No active session found for remote connection: {args.connection_string}"
                    )]

            elif name == "close_windbg_attach":
                # no args needed; keep the parsing for symmetry/validation
                _ = CloseWindbgAttachParams(**arguments)
                success = unload_session("__windbg_attach__")
                if success:
                    return [TextContent(
                        type="text",
                        text=f"Successfully closed remote connection: {args.connection_string}"
                    )]
                else:
                    return [TextContent(
                        type="text",
                        text=f"No active session found for remote connection: {args.connection_string}"
                    )]

            elif name == "list_windbg_dumps":
                args = ListWindbgDumpsParams(**arguments)
                
                if args.directory_path is None:
                    args.directory_path = get_local_dumps_path()
                    if args.directory_path is None:
                        raise McpError(ErrorData(
                            code=INVALID_PARAMS,
                            message="No directory path specified and no default dump path found in registry."
                        ))
                
                if not os.path.exists(args.directory_path) or not os.path.isdir(args.directory_path):
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS,
                        message=f"Directory not found: {args.directory_path}"
                    ))
                
                # Determine search pattern based on recursion flag
                search_pattern = os.path.join(args.directory_path, "**", "*.*dmp") if args.recursive else os.path.join(args.directory_path, "*.*dmp")
                
                # Find all dump files
                dump_files = glob.glob(search_pattern, recursive=args.recursive)
                
                # Sort alphabetically for consistent results
                dump_files.sort()
                
                if not dump_files:
                    return [TextContent(
                        type="text",
                        text=f"No crash dump files (*.*dmp) found in {args.directory_path}"
                    )]
                
                # Format the results
                result_text = f"Found {len(dump_files)} crash dump file(s) in {args.directory_path}:\n\n"
                for i, dump_file in enumerate(dump_files):
                    # Get file size in MB
                    try:
                        size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
                    except (OSError, IOError):
                        size_mb = "unknown"
                    
                    result_text += f"{i+1}. {dump_file} ({size_mb} MB)\n"
                
                return [TextContent(
                    type="text",
                    text=result_text
                )]
            
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown tool: {name}"
            ))
            
        except McpError:
            raise
        except Exception as e:
            traceback_str = traceback.format_exc()
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Error executing tool {name}: {str(e)}\n{traceback_str}"
            ))
            
    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)

# Clean up function to ensure all sessions are closed when the server exits
def cleanup_sessions():
    """Close all active CDB sessions."""
    for dump_path, session in active_sessions.items():
        try:
            if session is not None:
                session.shutdown()
        except Exception:
            pass
    active_sessions.clear()

# Register cleanup on module exit
import atexit
atexit.register(cleanup_sessions)
