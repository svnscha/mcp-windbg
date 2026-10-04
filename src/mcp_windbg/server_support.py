"""Tool schemas, discovery and presentation helpers for the MCP server."""

import os
import winreg
from typing import List, Optional

from mcp.shared.exceptions import MCPError
from mcp.types import (
    GetPromptResult, INTERNAL_ERROR, INVALID_PARAMS, ListPromptsResult, ListToolsResult,
    Prompt, PromptArgument, PromptMessage, TextContent, Tool,
)
from pydantic import BaseModel, Field

from .debug_session import DEFAULT_WAIT_FOR_BREAK_TIMEOUT
from .prompts import load_prompt

# --- Per-tool-call timeout defaults (seconds) --------------------------------
# A tool's effective timeout is: the call's `timeout_seconds` if given, else the
# larger of the tool default below and the server-wide `--timeout` floor. These
# defaults reflect how long each operation realistically takes:
#   - a dump's !analyze -v can run for a while,
#   - a KDNET memory read can be slow, especially on a flaky link.
CDB_DUMP_OPEN_TIMEOUT = 180
CDB_REMOTE_OPEN_TIMEOUT = 60
KD_OPEN_TIMEOUT = 60
KD_DUMP_OPEN_TIMEOUT = 180
CDB_COMMAND_TIMEOUT = 60
KD_COMMAND_TIMEOUT = 120
# wait_for_break is not a command timeout: it is how long we are willing to sit
# on a running target waiting for it to bugcheck or hit a breakpoint.
WAIT_FOR_BREAK_TIMEOUT = DEFAULT_WAIT_FOR_BREAK_TIMEOUT


def _effective_timeout(per_call: Optional[int], tool_default: int, server_timeout: int) -> int:
    """Resolve a call's timeout: explicit override, else max(default, floor)."""
    if per_call and per_call > 0:
        return per_call
    return max(tool_default, server_timeout)


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


class ListDumps(BaseModel):
    """Parameters for listing crash dumps in a directory."""
    directory_path: Optional[str] = Field(
        default=None,
        description="Directory to search for dump files. Defaults to the configured dump path from the registry."
    )
    recursive: bool = Field(default=False, description="Search subdirectories recursively.")
    offset: int = Field(default=0, ge=0, description="Skip this many results in filesystem enumeration order; refresh if files change.")
    limit: int = Field(default=50, ge=1, le=1000, description="Maximum dump paths and sizes returned per page (default 50, maximum 1000).")


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


class OpenKdDump(BaseModel):
    """Parameters for opening a kernel crash dump (kd.exe)."""
    dump_path: str = Field(description="Path to the kernel-mode crash dump, e.g. C:\\Windows\\MEMORY.DMP or a file in C:\\Windows\\Minidump")
    symbols_path: Optional[str] = Field(default=None, description="Additional symbol search path for PDB resolution.")
    include_stack_trace: bool = Field(default=False, description="Include a stack trace (kb) in the initial analysis.")
    include_modules: bool = Field(default=False, description="Include loaded modules (lm) in the initial analysis.")
    include_threads: bool = Field(default=False, description="Include threads (~) in the initial analysis.")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the timeout (seconds) for opening/analyzing this dump.")


class RunCdbCommand(BaseModel):
    """Parameters for running a command on a user-mode (cdb) session."""
    session_id: str = Field(description="A cdb session_id returned by open_cdb_dump or open_cdb_remote.")
    command: str = Field(description="WinDbg/CDB command to execute (e.g. 'kb', 'lm', '!analyze -v').")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the command timeout (seconds).")


class RunKdCommand(BaseModel):
    """Parameters for running a command on a kernel (kd) session."""
    session_id: str = Field(description="A kd session_id returned by open_kd_session or open_kd_dump.")
    command: str = Field(description="Kernel debugger command to execute (e.g. '!process 0 0', 'vertarget', '!analyze -v').")
    timeout_seconds: Optional[int] = Field(default=None, description="Override the command timeout (seconds).")


class CloseCdbSession(BaseModel):
    """Parameters for closing a user-mode (cdb) session."""
    session_id: str = Field(description="The cdb session_id to close.")


class CloseKdSession(BaseModel):
    """Parameters for closing a kernel (kd) session."""
    session_id: str = Field(description="The kd session_id to close.")
    resume: bool = Field(default=True, description="Resume the target machine on close (send 'g' so it runs again); ignored for a dump. Set false to intentionally leave it halted at the break - note that freezes the whole machine until a debugger resumes it.")


class SendCtrlBreak(BaseModel):
    """Parameters for breaking into a running session."""
    session_id: str = Field(description="A live session_id (cdb remote or kd) to break into.")


class WaitForBreak(BaseModel):
    """Parameters for waiting until a resumed target stops."""
    session_id: str = Field(description="A live session_id (cdb remote or kd) whose target is running.")
    timeout_seconds: Optional[int] = Field(default=None, description=f"Maximum seconds to wait (default {WAIT_FOR_BREAK_TIMEOUT}). The target stops on a crash/bugcheck, a breakpoint, or a CTRL+BREAK from elsewhere.")


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


def _init_sections(session, init_commands: Optional[List[str]], timeout: int) -> list[str]:
    """Run the server's init commands on a new session, before any triage."""
    if not init_commands:
        return []
    lines = []
    for command in init_commands:
        lines.append(f"> {command}")
        lines.extend(session.send_command(command, timeout=timeout))
    return ["### Initialization\n```\n" + "\n".join(lines) + "\n```\n\n"]


def _is_kernel_dump(dump_path: str) -> bool:
    """True for a kernel crash dump, whatever debugger opens it."""
    try:
        with open(dump_path, "rb") as handle:
            # Kernel dumps start PAGEDUMP/PAGEDU64; user-mode minidumps start MDMP.
            return handle.read(4) == b"PAGE"
    except OSError:
        return False


async def on_list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=[
        Tool(
            name="list_dumps",
            description="""
            List Windows crash dump files in a directory.
            Helps discover dumps to analyze with open_cdb_dump (or open_kd_dump for kernel dumps).
            """,
            inputSchema=ListDumps.model_json_schema(),
        ),
        Tool(
            name="open_cdb_dump",
            description="""
            Open and triage a Windows crash dump with cdb.exe (user mode).
            Runs .lastevent and !analyze -v (optionally kb/lm/~) and returns a session_id.
            Use that session_id with run_cdb_command and close_cdb_session.
            For a kernel dump from a bugcheck (MEMORY.DMP, Minidump\\*.dmp) use open_kd_dump.
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
            name="open_kd_dump",
            description="""
            Open and triage a kernel-mode crash dump with kd.exe: a complete, kernel, or bitmap
            memory dump (MEMORY.DMP) or a small memory dump (Minidump\\*.dmp) written by a bugcheck.
            Runs vertarget and !analyze -v (optionally kb/lm/~) and returns a session_id
            for run_kd_command and close_kd_session. For user-mode dumps use open_cdb_dump.
            """,
            inputSchema=OpenKdDump.model_json_schema(),
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
            Run a command on a kernel session (from open_kd_session or open_kd_dump), addressed by session_id.
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
        Tool(
            name="wait_for_break",
            description="""
            Block until a resumed target stops, and return everything it printed when it did.
            Use this after letting the target run with 'g' - to catch the bugcheck, the
            breakpoint report, or the break-in banner. Returns immediately if the target is
            already stopped. If it is still running when the wait expires, the target is left
            running: wait again, or halt it with send_ctrl_break.
            """,
            inputSchema=WaitForBreak.model_json_schema(),
        ),
    ])


# One entry per <name>.prompt.md; an optional target is pinned to the prompt.
PROMPT_SPECS = {
    "dump-triage": {
        "title": "Crash Dump Triage Analysis",
        "description": "Comprehensive single crash dump analysis with detailed metadata extraction and structured reporting",
        "argument": "dump_path",
        "argument_description": "Path to the Windows crash dump file to analyze (optional - will prompt if not provided)",
        "label": "Dump file to analyze",
    },
    "remote-triage": {
        "title": "Live Target Investigation",
        "description": "Investigate a live user-mode target through a cdb debugging server: break in, orient, and track down a hang or crash",
        "argument": "connection_string",
        "argument_description": "The -remote connection string, e.g. tcp:Port=5005,Server=192.168.0.100 (optional - will prompt if not provided)",
        "label": "Target to connect to",
    },
    "kernel-triage": {
        "title": "Kernel Target Investigation",
        "description": "Investigate a live kernel target over a -k connection: orient, track down a bugcheck or hang, and release the machine",
        "argument": "connection_string",
        "argument_description": "The -k connection string, e.g. net:port=50000,key=1.2.3.4 (optional - will prompt if not provided)",
        "label": "Kernel target to connect to",
    },
}


async def on_list_prompts(ctx, params) -> ListPromptsResult:
    return ListPromptsResult(prompts=[
        Prompt(
            name=name,
            title=spec["title"],
            description=spec["description"],
            arguments=[
                PromptArgument(
                    name=spec["argument"],
                    description=spec["argument_description"],
                    required=False,
                ),
            ],
        )
        for name, spec in PROMPT_SPECS.items()
    ])


async def on_get_prompt(ctx, params) -> GetPromptResult:
    name, arguments = params.name, params.arguments or {}

    spec = PROMPT_SPECS.get(name)
    if spec is None:
        raise MCPError(INVALID_PARAMS, f"Unknown prompt: {name}")

    try:
        prompt_content = load_prompt(name)
    except FileNotFoundError as e:
        raise MCPError(INTERNAL_ERROR, f"Prompt file not found: {e}")

    target = arguments.get(spec["argument"], "")
    if target:
        prompt_text = f"**{spec['label']}:** {target}\n\n{prompt_content}"
    else:
        prompt_text = prompt_content

    return GetPromptResult(
        description=spec["description"],
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
