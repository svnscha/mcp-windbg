# MCP Server for WinDbg Crash Analysis

[![CI](https://github.com/svnscha/mcp-windbg/actions/workflows/ci.yml/badge.svg)](https://github.com/svnscha/mcp-windbg/actions/workflows/ci.yml)
[![Docs](https://github.com/svnscha/mcp-windbg/actions/workflows/pages.yml/badge.svg)](https://svnscha.github.io/mcp-windbg/)
[![PyPI](https://img.shields.io/pypi/v/mcp-windbg)](https://pypi.org/project/mcp-windbg/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)

A Model Context Protocol server that bridges AI models with WinDbg for crash dump analysis and remote debugging.

<!-- mcp-name: io.github.svnscha/mcp-windbg -->

## Overview

This MCP server integrates with [CDB](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/opening-a-crash-dump-file-using-cdb) to enable AI models to analyze Windows crash dumps and connect to remote debugging sessions using WinDbg/CDB.

## What is this?

An AI-powered tool that bridges LLMs with WinDbg for crash dump analysis and live debugging. Execute debugger commands through natural language queries like *"Show me the call stack and explain this access violation"*.

## What This is Not

Not a magical auto-fix solution. It's a Python wrapper around CDB that leverages LLM knowledge to assist with debugging.

## Features

- **Crash dump analysis**: open a `.dmp`/`.mdmp`/`.hdmp` and get automated triage (`!analyze -v`, stacks, modules, threads).
- **User-mode remote debugging**: attach to a live `cdb`/WinDbg debug server (`-remote`) over TCP, a named pipe, or COM.
- **Kernel debugging**: attach to a kernel target (`-k`) over KDNET, a named pipe, or serial.
- **Multi-dump triage**: discover and compare many dumps across a directory.

## Quick Start

### Prerequisites
- Windows with [Debugging Tools for Windows](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) or [WinDbg from Microsoft Store](https://apps.microsoft.com/detail/9pgjgd53tn86).
- Python 3.10 or higher
- Any MCP-compatible client (GitHub Copilot, Claude Desktop, Cline, Cursor, Windsurf etc.)
- Configure MCP server in your chosen client

> [!TIP]
> In enterprise environments, MCP server usage might be restricted by organizational policies. Check with your IT team about AI tool usage and ensure you have the necessary permissions before proceeding.

### Installation
```bash
pip install mcp-windbg
```

## Transport Options

The MCP server supports multiple transport protocols:

| Transport | Description | Use Case |
|-----------|-------------|----------|
| `stdio` (default) | Standard input/output | Local MCP clients like VS Code, Claude Desktop |
| `streamable-http` | Streamable HTTP | Modern HTTP clients with bidirectional streaming |

### Starting with Different Transports

**Standard I/O (default):**
```bash
mcp-windbg
# or explicitly
mcp-windbg --transport stdio
```

**Streamable HTTP:**
```bash
mcp-windbg --transport streamable-http --host 127.0.0.1 --port 8000
```
Endpoint: `http://127.0.0.1:8000/mcp`

### Command Line Options

```
--transport {stdio,streamable-http}  Transport protocol (default: stdio)
--host HOST                              HTTP server host (default: 127.0.0.1)
--port PORT                              HTTP server port (default: 8000)
--cdb-path PATH                          Custom path to cdb.exe
--symbols-path PATH                      Custom symbols path
--filter-script PATH                     Python script with process_input/process_output tool text hooks
--timeout SECONDS                        Baseline command/connect timeout, a floor for the per-tool defaults (default: 60)
--verbose                                Enable verbose output
```

### Filter Script Hooks

Use `--filter-script` to load a small Python helper that rewrites tool text only (for example, to redact PII) without seeing the full MCP JSON-RPC envelope:

```bash
mcp-windbg --filter-script C:\filters\pii_redaction.py
```

The script defines `process_input` and/or `process_output` callbacks and runs in-process, so treat it as trusted code. See [Redact sensitive data](https://svnscha.github.io/mcp-windbg/scenarios/redaction/) for the callback contract and a worked example.

## Configuration

`mcp-windbg` works with any MCP client. Two common setups are below; see the [client configuration guide](https://svnscha.github.io/mcp-windbg/reference/clients/) for Claude Desktop, Copilot CLI, Autohand Code, HTTP, and from-source.

**VS Code (GitHub Copilot)** - press `F1` and select **MCP: Open User Configuration** to enable it in every workspace:

```json
{
    "servers": {
        "mcp_windbg": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "mcp_windbg"],
            "env": {
                "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
            }
        }
    }
}
```

**Claude Code** - register the server from the command line:

```bash
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- python -m mcp_windbg
```

Prefer not to install the package? Replace `python -m mcp_windbg` with `uvx --from git+https://github.com/svnscha/mcp-windbg mcp-windbg` in either setup to fetch and run the server on demand.

Once configured, restart your MCP client and start debugging:

```
Analyze the crash dump at C:\dumps\app.dmp
```

## MCP Compatibility

This server implements the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), making it compatible with any MCP-enabled client:

The beauty of MCP is that you write the server once, and it works everywhere. Choose your favorite AI assistant!

### Tools

Every `open_*` tool returns an opaque **`session_id`** (e.g. `cdb-1a2b3c4d`); pass it to the matching `run_*`, `close_*`, and `send_ctrl_break` calls. User-mode targets (dumps and `-remote`) run under `cdb.exe`; kernel targets run under `kd.exe`.

| Tool | Purpose | Use Case |
|------|---------|----------|
| `list_dumps` | List crash dump files | Discovery and batch analysis |
| `open_cdb_dump` | Open and triage a crash dump (`cdb.exe`) | Initial crash dump analysis → `session_id` |
| `open_cdb_remote` | Attach to a user-mode remote debug server (`-remote`) | Live user-mode sessions → `session_id` |
| `open_kd_session` | Attach to a kernel target (`-k`, `kd.exe`) | KDNET, named pipe, or serial → `session_id` |
| `run_cdb_command` | Run a command on a user-mode session | Custom analysis, by `session_id` |
| `run_kd_command` | Run a command on a kernel session | Kernel investigation, by `session_id` |
| `close_cdb_session` | Close a user-mode session | Resource management, by `session_id` |
| `close_kd_session` | Close a kernel session | Resource management, by `session_id` |
| `send_ctrl_break` | Break into a running live session | Interrupt a running target, by `session_id` |

Each `run_*` / `open_*` call accepts an optional `timeout_seconds` to override the per-tool default (`open_cdb_dump` 180s, `run_cdb_command` 60s, `run_kd_command` 120s, connects 60s). On a live session a command that outruns its timeout is broken into with CTRL+BREAK and the session is resynchronized, so it never wedges.

## Documentation

**[Documentation](https://svnscha.github.io/mcp-windbg/)**

| Topic | Description |
|-------|-------------|
| **[Getting Started](https://svnscha.github.io/mcp-windbg/getting-started/)** | Quick setup and first crash dump analysis |
| **[Use cases](https://svnscha.github.io/mcp-windbg/scenarios/)** | Analyze a dump, debug a remote or kernel target, triage many dumps |
| **[Command-line options](https://svnscha.github.io/mcp-windbg/reference/cli/)** | Every CLI flag, transports, and filter hooks |
| **[Tools Reference](https://svnscha.github.io/mcp-windbg/reference/tools/)** | The MCP tools and their parameters |
| **[Client configuration](https://svnscha.github.io/mcp-windbg/reference/clients/)** | VS Code, Claude Desktop, Copilot CLI, pip, and source |
| **[Troubleshooting](https://svnscha.github.io/mcp-windbg/troubleshooting/)** | Common issues and solutions |
| **[Development](https://svnscha.github.io/mcp-windbg/development/)** | Run from a local checkout and point a client at the dev build |

## Examples

### Crash Dump Analysis

> Analyze this heap address with !heap -p -a 0xABCD1234 and check for buffer overflow"

> Execute !peb and tell me if there are any environment variables that might affect this crash"

> Run .ecxr followed by k and explain the exception's root cause"

### Remote Debugging

> "Connect to tcp:Port=5005,Server=192.168.0.100 and show me the current thread state"

> "Send CTRL+BREAK to the live session, then dump all thread stacks with ~*k"

> "Check for timing issues in the thread pool with !runaway and !threads"

> "Show me all threads with ~*k and identify which one is causing the hang"

### Kernel Debugging

> "Open a kernel session on net:port=50000,key=1.2.3.4 and show the target version" (returns a `session_id`)

> "Using that session, break in, run !analyze -v, and tell me which driver caused the bugcheck"

## Blog

Read about the development journey: [The Future of Crash Analysis: AI Meets WinDbg](https://svnscha.de/posts/ai-meets-windbg/)

### Links

- [Reddit: I taught Copilot to analyze Windows Crash Dumps](https://www.reddit.com/r/programming/comments/1kes3wq/i_taught_copilot_to_analyze_windows_crash_dumps/)
- [Hackernews: AI Meets WinDbg](https://news.ycombinator.com/item?id=43892096)

## Star History

<a href="https://www.star-history.com/?type=date&repos=svnscha%2Fmcp-windbg">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=svnscha/mcp-windbg&type=date&theme=dark&legend=top-left&sealed_token=lsqF7R-C6fvFwsFqhZrlCZCPWspD7aYxHye-VjtTYGbAqLpc1PAHHZbRdRJXf1dKHEz6JOjWEKcf-pMf46XIb7YsnXpwx5LxeRjUEWB8wDHl4Z5uUJN78DpbVL4BjxyfhSF1P3qiq4f4-eHdE7nTjK-Yp_kw1fi3S2WaP-siJv_kCIV_VE67SwRMrmRa" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=svnscha/mcp-windbg&type=date&legend=top-left&sealed_token=lsqF7R-C6fvFwsFqhZrlCZCPWspD7aYxHye-VjtTYGbAqLpc1PAHHZbRdRJXf1dKHEz6JOjWEKcf-pMf46XIb7YsnXpwx5LxeRjUEWB8wDHl4Z5uUJN78DpbVL4BjxyfhSF1P3qiq4f4-eHdE7nTjK-Yp_kw1fi3S2WaP-siJv_kCIV_VE67SwRMrmRa" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=svnscha/mcp-windbg&type=date&legend=top-left&sealed_token=lsqF7R-C6fvFwsFqhZrlCZCPWspD7aYxHye-VjtTYGbAqLpc1PAHHZbRdRJXf1dKHEz6JOjWEKcf-pMf46XIb7YsnXpwx5LxeRjUEWB8wDHl4Z5uUJN78DpbVL4BjxyfhSF1P3qiq4f4-eHdE7nTjK-Yp_kw1fi3S2WaP-siJv_kCIV_VE67SwRMrmRa" />
 </picture>
</a>

## License

MIT
