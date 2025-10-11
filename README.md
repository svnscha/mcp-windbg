# MCP Server for WinDbg Crash Analysis

A Model Context Protocol server that bridges AI models with WinDbg for crash dump analysis and remote debugging.

<!-- mcp-name: io.github.svnscha/mcp-windbg -->

## Overview

This MCP server integrates with [CDB](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/opening-a-crash-dump-file-using-cdb) to enable AI models to analyze Windows crash dumps and connect to remote debugging sessions using WinDbg/CDB.

## What is this?

**Primarily**, a tool that enables AI to interact with WinDbg for both crash dump analysis and live debugging. The "magic" is giving LLMs the ability to execute debugger commands on crash dumps or remote debugging targets.

This means you can:
- Get immediate first-level triage analysis for categorizing crash dumps
- Perform natural language-based analysis: *"Show me the call stack and explain what might be causing this access violation"*
- Auto-analyze simple cases and get insights for complex debugging scenarios
- Connect to live debugging sessions for real-time analysis

**What this is NOT**: A magical solution that automatically fixes all issues. It's a **simple Python wrapper around CDB** that **relies** on the **LLM's WinDbg** expertise.

## Quick Start

### Prerequisites
- Windows with [Debugging Tools for Windows](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) or [WinDbg from Microsoft Store](https://apps.microsoft.com/detail/9pgjgd53tn86).
- Python 3.10 or higher
- VS Code with GitHub Copilot (recommended)
- Enable [MCP Server in VS Code](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)

> [!TIP]
> In enterprise environments, MCP usage might be restricted by organizational policies. Ensure you have the necessary permissions before proceeding.

### Installation
```bash
pip install mcp-windbg
```

### Configuration
Create `.vscode/mcp.json` in your workspace:
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

Ensure the MCP server is started and start with:

```
Analyze the crash dump at C:\dumps\app.dmp
```

## Tools

| Tool | Purpose | Use Case |
|------|---------|----------|
| [`list_windbg_dumps`](#list_windbg_dumps) | List crash dump files | Discovery and batch analysis |
| [`open_windbg_dump`](#open_windbg_dump) | Analyze crash dumps | Initial crash dump analysis |
| [`close_windbg_dump`](#close_windbg_dump) | Cleanup dump sessions | Resource management |
| [`open_windbg_remote`](#open_windbg_remote) | Connect to remote debugging | Live debugging sessions |
| [`close_windbg_remote`](#close_windbg_remote) | Cleanup remote sessions | Resource management |
| [`run_windbg_cmd`](#run_windbg_cmd) | Execute WinDbg commands | Custom analysis and investigation |

## Documentation

**[Documentation](https://github.com/svnscha/mcp-windbg/wiki)**

| Topic | Description |
|-------|-------------|
| **[Getting Started](https://github.com/svnscha/mcp-windbg/wiki/Getting-Started)** | Quick setup and first steps |
| **[Installation](https://github.com/svnscha/mcp-windbg/wiki/Installation)** | Detailed installation for pip, MCP registry, and from source |
| **[Usage](https://github.com/svnscha/mcp-windbg/wiki/Usage)** | VS Code integration, command-line usage, and workflows |
| **[Tools Reference](https://github.com/svnscha/mcp-windbg/wiki/Tools)** | Complete API reference and examples |
| **[Troubleshooting](https://github.com/svnscha/mcp-windbg/wiki/Troubleshooting)** | Common issues and solutions |

## Examples

### Crash Dump Analysis

> Analyze this heap address with !heap -p -a 0xABCD1234 and check for buffer overflow"

> Execute !peb and tell me if there are any environment variables that might affect this crash"

> Run .ecxr followed by k and explain the exception's root cause"

### Remote Debugging

> "Connect to tcp:Port=5005,Server=192.168.0.100 and show me the current thread state"

> "Check for timing issues in the thread pool with !runaway and !threads"

> "Show me all threads with ~*k and identify which one is causing the hang"

## Blog

Read about the development journey: [The Future of Crash Analysis: AI Meets WinDbg](https://svnscha.de/posts/ai-meets-windbg/)

### Links

- [Reddit: I taught Copilot to analyze Windows Crash Dumps](https://www.reddit.com/r/programming/comments/1kes3wq/i_taught_copilot_to_analyze_windows_crash_dumps/)
- [Hackernews: AI Meets WinDbg](https://news.ycombinator.com/item?id=43892096)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=svnscha/mcp-windbg&type=Date)](https://www.star-history.com/#svnscha/mcp-windbg&Date)

## License

MIT
