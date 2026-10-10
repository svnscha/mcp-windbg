"""MCP tool schemas, tool descriptions, and packaged prompt handlers."""

from typing import Optional

from mcp.shared.exceptions import MCPError
from mcp.types import (
    GetPromptResult, ListPromptsResult, ListToolsResult, Prompt, PromptArgument,
    PromptMessage, TextContent, Tool, INVALID_PARAMS, INTERNAL_ERROR,
)
from pydantic import BaseModel, Field

from .debug_session import DEFAULT_WAIT_FOR_BREAK_TIMEOUT as WAIT_FOR_BREAK_TIMEOUT
from .prompts import load_prompt


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
            with 'cdb -server tcp:port=5005 -noio <program>'. Returns a session_id for run_cdb_command
            / send_ctrl_break / close_cdb_session. For kernel targets use open_kd_session instead.
            WARNING: CDB 10.0.29661.1004 in -server mode with NUL stdin (/dev/null, subprocess.DEVNULL), or a
            stdin pipe at EOF, can exhaust Windows nonpaged pool and freeze the host. For
            unattended startup put -server and its transport first, then -noio. This disables
            local console I/O; use the remote client for commands and output. Apply -noio only
            to the external -server process, never to the MCP-managed -remote client. Never use NUL/EOF
            stdin without -noio. Arrange cleanup before launching; record the spawned server PID.
            close_cdb_session closes only the MCP client, not the external server. Stop only
            servers you started; leave pre-existing servers alone. If 'Could not write to pipe,
            1450' repeats, stop your owned server instead of retrying. Details:
            https://github.com/microsoft/WinDbg-Feedback/issues/402
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
            For a remote session this closes only the MCP client, not the external debug server.
            Stop any server you started separately; leave pre-existing servers alone.
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

# One entry per <name>.prompt.md in prompts/. Each takes a single optional
# argument; when the caller supplies it, it is pinned to the top of the prompt
# text so the model starts with the target instead of asking for it.
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
