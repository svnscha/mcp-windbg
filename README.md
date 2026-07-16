# MCP Server for WinDbg Crash Analysis

[![CI](https://github.com/svnscha/mcp-windbg/actions/workflows/ci.yml/badge.svg)](https://github.com/svnscha/mcp-windbg/actions/workflows/ci.yml)
[![Docs](https://github.com/svnscha/mcp-windbg/actions/workflows/pages.yml/badge.svg)](https://svnscha.github.io/mcp-windbg/)
[![PyPI](https://img.shields.io/pypi/v/mcp-windbg)](https://pypi.org/project/mcp-windbg/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)

A Model Context Protocol server that bridges AI models with WinDbg for crash dump analysis, user-mode remote debugging, and kernel debugging.

<!-- mcp-name: io.github.svnscha/mcp-windbg -->

## Overview

This server drives the Windows debuggers - [CDB](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/opening-a-crash-dump-file-using-cdb) for user mode (dumps and `-remote`) and **KD** for kernel targets (`-k`) - so you can debug in natural language: *"Show me the call stack and explain this access violation"* or *"Open a kernel session and tell me which driver bugchecked."*

It is not a magical auto-fix. It is a Python wrapper around `cdb.exe` / `kd.exe` that lets an LLM run real debugger commands and reason about the output.

## Features

- **Crash dump analysis** - open a `.dmp`/`.mdmp`/`.hdmp` and get automated triage (`!analyze -v`, stacks, modules, threads) in a single call.
- **User-mode remote debugging** - attach to a live `cdb`/WinDbg debug server (`-remote`) over TCP, a named pipe, or COM, and break in on demand.
- **Kernel debugging** - attach to a kernel target (`-k`, driven by `kd.exe`) over KDNET, a named pipe, or serial; the server waits for the target and breaks in for you.
- **Run any WinDbg/KD command** - drive an open session with arbitrary commands (`kb`, `!process 0 0`, `!heap`, `lm`, ...) described in natural language.
- **Session ids** - every open returns a session id; several sessions (dumps, remote, kernel) can be open at once and are addressed independently.
- **Resilient live sessions** - per-call timeouts, and a slow live command that outruns its timeout is broken into with CTRL+BREAK and the session resynchronized instead of wedging.
- **Multi-dump triage** - discover and compare many dumps across a directory.
- **Text filter hooks** - a `--filter-script` can redact PII/secrets from tool arguments and output before they leave the machine.
- **stdio or HTTP** - run locally over stdio, or as a streamable-HTTP service you drive from another machine.

## Use cases

| You have | You want to | Guide |
| --- | --- | --- |
| A `.dmp` from a crash | Root-cause it: exception, faulting frame, why it happened | [Analyze a crash dump](https://svnscha.github.io/mcp-windbg/scenarios/crash-dump/) |
| A live user-mode process (via `cdb -server`) | Break in and inspect a hang or live state | [Debug a remote target](https://svnscha.github.io/mcp-windbg/scenarios/remote-debugging/) |
| A KD-enabled machine or VM | Debug drivers, bugchecks, and boot-time issues | [Debug a kernel target](https://svnscha.github.io/mcp-windbg/scenarios/kernel-debugging/) |
| A folder full of dumps | Triage the batch and find the common signature | [Triage multiple dumps](https://svnscha.github.io/mcp-windbg/scenarios/triage/) |
| A debugging host, but you work elsewhere | Drive it over HTTP from another machine | [Debug from another machine](https://svnscha.github.io/mcp-windbg/scenarios/http-service/) |
| Dumps with secrets or PII | Scrub tool output before it leaves the box | [Redact sensitive data](https://svnscha.github.io/mcp-windbg/scenarios/redaction/) |

## Tools

Every `open_*` tool returns an opaque **`session_id`** (e.g. `cdb-1a2b3c4d`); pass it to the matching `run_*`, `close_*`, and `send_ctrl_break` calls. User-mode targets (dumps and `-remote`) run under `cdb.exe`; kernel targets run under `kd.exe`.

| Tool | Purpose |
|------|---------|
| `list_dumps` | List crash dump files in a directory |
| `open_cdb_dump` | Open and triage a crash dump |
| `open_cdb_remote` | Attach to a user-mode remote debug server (`-remote`) |
| `open_kd_session` | Attach to a kernel target (`-k`, KDNET / named pipe / serial) |
| `run_cdb_command` | Run a command on a user-mode session |
| `run_kd_command` | Run a command on a kernel session |
| `close_cdb_session` | Close a user-mode session |
| `close_kd_session` | Close a kernel session (resumes the target machine) |
| `send_ctrl_break` | Break into a running live session |

Parameters, timeouts, and the built-in triage prompts are in the [tools reference](https://svnscha.github.io/mcp-windbg/reference/tools/).

## Quick start

**Prerequisites**

- Windows with [Debugging Tools for Windows](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) or [WinDbg from the Microsoft Store](https://apps.microsoft.com/detail/9pgjgd53tn86), which ship `cdb.exe` and `kd.exe` (auto-detected).
- Python 3.10 or higher.
- Any MCP-compatible client (Claude Code, GitHub Copilot, Claude Desktop, Cursor, Windsurf, Cline, ...).

> [!TIP]
> In enterprise environments, MCP server usage might be restricted by organizational policies. Check with your IT team about AI tool usage and ensure you have the necessary permissions before proceeding.

**Install**

```bash
pip install mcp-windbg
```

**Configure your client.** The two most common setups are below; see the [client configuration guide](https://svnscha.github.io/mcp-windbg/reference/clients/) for Claude Desktop, Copilot CLI, Autohand Code, HTTP, and from-source.

Claude Code - register the server from the command line:

```bash
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- python -m mcp_windbg
```

VS Code (GitHub Copilot) - press `F1` and select **MCP: Open User Configuration** to enable it in every workspace:

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

Restart your client, then start debugging:

```text
Analyze the crash dump at C:\dumps\app.dmp
Connect to tcp:Port=5005,Server=192.168.0.100 and show me the current thread state
Open a kernel session on net:port=50000,key=1.2.3.4, run !analyze -v, and tell me which driver bugchecked
```

Server options (`--cdb-path`, `--kd-path`, `--symbols-path`, `--filter-script`, `--transport`, ...) are documented in the [command-line reference](https://svnscha.github.io/mcp-windbg/reference/cli/).

## Documentation

**[svnscha.github.io/mcp-windbg](https://svnscha.github.io/mcp-windbg/)**

| Topic | Description |
|-------|-------------|
| **[Getting started](https://svnscha.github.io/mcp-windbg/getting-started/)** | Setup and your first crash dump analysis |
| **[Analyze a crash dump](https://svnscha.github.io/mcp-windbg/scenarios/crash-dump/)** | Root-cause an exception: faulting frame, why it happened |
| **[Debug a remote target](https://svnscha.github.io/mcp-windbg/scenarios/remote-debugging/)** | Break into a live user-mode process and inspect a hang |
| **[Debug a kernel target](https://svnscha.github.io/mcp-windbg/scenarios/kernel-debugging/)** | Drivers, bugchecks, and boot-time issues over KDNET or a pipe |
| **[Triage multiple dumps](https://svnscha.github.io/mcp-windbg/scenarios/triage/)** | Scan a folder and find the common signature |
| **[Debug from another machine](https://svnscha.github.io/mcp-windbg/scenarios/http-service/)** | Run the server over HTTP and drive it remotely |
| **[Redact sensitive data](https://svnscha.github.io/mcp-windbg/scenarios/redaction/)** | Scrub secrets from tool output before it leaves the box |
| **[Reference](https://svnscha.github.io/mcp-windbg/reference/)** | Tools, prompts, CLI options, and client configuration |
| **[Troubleshooting](https://svnscha.github.io/mcp-windbg/troubleshooting/)** | Common issues and solutions |
| **[Development](https://svnscha.github.io/mcp-windbg/development/)** | Run from a local checkout and point a client at the dev build |

## Blog

Read about the development journey: [The Future of Crash Analysis: AI Meets WinDbg](https://svnscha.de/posts/ai-meets-windbg/)

- [Reddit: I taught Copilot to analyze Windows Crash Dumps](https://www.reddit.com/r/programming/comments/1kes3wq/i_taught_copilot_to_analyze_windows_crash_dumps/)
- [Hackernews: AI Meets WinDbg](https://news.ycombinator.com/item?id=43892096)

## License

MIT
