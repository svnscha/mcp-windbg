# MCP Server for WinDBG Crash Analysis

A Model Context Protocol server that bridges AI models with WinDBG for crash dump analysis and remote debugging.

<!-- mcp-name: io.github.svnscha/mcp-windbg -->

## Overview

This MCP server integrates with [CDB](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/opening-a-crash-dump-file-using-cdb) to enable AI models to analyze Windows crash dumps and connect to remote debugging sessions using WinDBG/CDB.

## What is this?

**Primarily**, a tool that enables AI to interact with WinDBG for both crash dump analysis and live debugging. The "magic" is giving LLMs the ability to execute debugger commands on crash dumps or remote debugging targets.

This means you can:
- Get immediate first-level triage analysis for categorizing crash dumps
- Perform natural language-based analysis: *"Show me the call stack and explain what might be causing this access violation"*
- Auto-analyze simple cases and get insights for complex debugging scenarios
- Connect to live debugging sessions for real-time analysis

**What this is NOT**: A magical solution that automatically fixes all issues. It's a **simple Python wrapper around CDB** that **relies** on the **LLM's WinDBG** expertise.

## Quick Start

### Prerequisites
- Windows with [Debugging Tools for Windows](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) installed
- Python 3.10 or higher
- VS Code with GitHub Copilot (recommended)

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

Enable "Model Context Protocol in Chat" in Copilot settings, restart VS Code, and start analyzing:
```
@copilot Analyze the crash dump at C:\dumps\app.dmp
```

## Tools

- **`list_windbg_dumps`** - List crash dump files in directories
- **`open_windbg_dump`** - Analyze Windows crash dumps with automatic `!analyze -v`
- **`open_windbg_remote`** - Connect to remote debugging sessions
- **`run_windbg_cmd`** - Execute any WinDBG command on dumps or remote sessions
- **`close_windbg_dump`** / **`close_windbg_remote`** - Clean up resources

## Documentation

📖 **[Complete Documentation Wiki](../../wiki)**

| Topic | Description |
|-------|-------------|
| **[Getting Started](../../wiki/Getting-Started)** | Quick setup and first steps |
| **[Installation](../../wiki/Installation)** | Detailed installation for pip, MCP registry, and from source |
| **[Usage](../../wiki/Usage)** | VS Code integration, command-line usage, and workflows |
| **[Tools Reference](../../wiki/Tools-Reference)** | Complete API reference and examples |
| **[Troubleshooting](../../wiki/Troubleshooting)** | Common issues and solutions |

## Examples

### Crash Dump Analysis
```
"Analyze this heap address with !heap -p -a 0xABCD1234 and check for buffer overflow"
"Execute !peb and tell me if there are any environment variables that might affect this crash"
"Run .ecxr followed by k and explain the exception's root cause"
```

### Remote Debugging
```
"Connect to tcp:Port=5005,Server=192.168.0.100 and show me the current thread state"
"Check for timing issues in the thread pool with !runaway and !threads"
"Show me all threads with ~*k and identify which one is causing the hang"
```

## Blog

Read about the development journey: [The Future of Crash Analysis: AI Meets WinDBG](https://svnscha.de/posts/ai-meets-windbg/)

## License

MIT
