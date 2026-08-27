# Claude Code plugin

The plugin is the shortest way to use mcp-windbg from Claude Code. It brings the
[tools](tools.md), a set of skills, and an agent, and it needs no `pip install` and no MCP
configuration.

## Install

```
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg-uvx@mcp-windbg
```

The first line registers this repository as a plugin marketplace; the second installs the plugin
from it. Restart Claude Code if the tools do not appear straight away.

## What you get

All ten [tools](tools.md), plus:

| Skill | What it does |
| :---- | :----------- |
| `/mcp-windbg:analyze-dump` | Triage a crash dump - exception, faulting frame, and what the evidence supports |
| `/mcp-windbg:debug-remote` | Attach to a running process through a WinDbg debug server |
| `/mcp-windbg:kernel-debug` | Drive a live kernel target, including resuming it and waiting for a break |
| `/mcp-windbg:windbg-doctor` | Check whether this machine can debug at all, and what to fix |

And one agent:

- **`crash-analyst`** investigates a dump on its own and reports back a verdict, the evidence
  behind it, what it ruled out, and what to do next. Use it when a dump needs more than a first
  look, or to keep a long analysis out of your main conversation.

Ask for it by name: *"use the crash-analyst agent on C:\dumps\app.dmp"*.

## Requirements

- **Windows.** The server drives `cdb.exe` and `kd.exe`. A plugin manifest has no field for
  declaring a platform, so this is not enforced at install time - on macOS or Linux the plugin
  loads and the server then fails to find a debugger.
- **Debugging Tools for Windows**, for `cdb.exe`. Install [WinDbg](https://aka.ms/windbg) from the
  Microsoft Store, or the Windows SDK.
- **[uv](https://docs.astral.sh/uv/)**, which provides `uvx`. This is the one prerequisite the
  plugin cannot supply for you: `winget install astral-sh.uv`.

Run `/mcp-windbg:windbg-doctor` if you are unsure - it checks all three and tells you what is
missing.

## How it runs the server

The plugin does not require the package to be installed. It launches the server with `uvx`,
pinned to the release that matches the plugin version:

```json title="plugins/mcp-windbg/.mcp.json"
{
  "mcpServers": {
    "mcp-windbg": {
      "command": "uvx",
      "args": ["mcp-windbg@1.1.0"],
      "env": {
        "_NT_SYMBOL_PATH": "${_NT_SYMBOL_PATH:-SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols}"
      }
    }
  }
}
```

`uvx` fetches that version from PyPI on first use and caches it. Pinning keeps the plugin and the
server it drives in step, so a plugin update is what moves the server version.

## Symbols

Symbols work out of the box. The `${VAR:-default}` form above means:

- **No `_NT_SYMBOL_PATH` set** - the plugin supplies the Microsoft symbol server, caching to
  `C:\Symbols`.
- **`_NT_SYMBOL_PATH` already set** - your value wins and the default is ignored. A symbol path
  you tuned yourself is never overwritten.

To use your own, set it in the environment before starting Claude Code:

```powershell
setx _NT_SYMBOL_PATH "SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols;C:\my\pdbs"
```

`setx` only affects new processes, so restart the terminal. Check what the server actually
received with `/mcp`.

Without symbols, stacks resolve to `module+0x1234` and any analysis built on them is guesswork -
which is why the plugin defaults to something that works rather than to nothing.

## Passing other options

The [command-line options](cli.md) are set in the plugin's `.mcp.json` `args`. To add one - a
custom `--cdb-path`, a `--filter-script`, a longer `--timeout` - edit that file in the installed
plugin, or install the server directly instead (see
[Client configuration](clients.md#registering-the-server-directly)), which gives you full control
of the command line at the cost of the bundled skills and agent.

Note that editing the installed copy is overwritten when the plugin updates.

## Updating and removing

```
/plugin marketplace update mcp-windbg
/plugin update mcp-windbg-uvx@mcp-windbg
```

To remove it:

```
/plugin uninstall mcp-windbg-uvx@mcp-windbg
/plugin marketplace remove mcp-windbg
```

## Not using uv

Install the package yourself and register the server directly. You lose the skills and the agent,
but the tools are identical:

```powershell
pip install mcp-windbg
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- python -m mcp_windbg
```
