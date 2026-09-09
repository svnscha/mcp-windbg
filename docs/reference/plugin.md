# Claude Code plugins

Install the server plugin if needed, then add the skills only if you want them:

| Plugin | Server | Included workflows |
| --- | --- | --- |
| `mcp-windbg-uvx` | Launches the pinned server with uvx | `crash-analyst` agent; no skills |
| `mcp-windbg-skills` | Uses the uvx plugin or your own MCP connection | Four optional skills |

!!! note "Enterprise environments"
    Managed `allowedMcpServers` settings can cause Claude Code to silently skip
    plugin-bundled MCP servers ([issue #32882](https://github.com/anthropics/claude-code/issues/32882)).
    I recommend [manual server installation and registration](clients.md#registering-the-server-directly)
    plus the [skills-only plugin](#skills-for-an-existing-server).
    The server must still be permitted by your organization's MCP policy.

## Install

### Server with uvx

```text
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg-uvx@mcp-windbg
```

This plugin supplies the ten [tools](tools.md), an agent, and default symbol
settings. It needs Windows with [Debugging Tools for Windows](https://aka.ms/windbg)
and [uv](https://docs.astral.sh/uv/) on `PATH` (`winget install astral-sh.uv`).
No separate Python or package installation is required. Skills are not included;
install the skills plugin below if you want them.

### Skills for an existing server

```text
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg-skills@mcp-windbg
```

First install the uvx plugin above, [register mcp-windbg directly](clients.md#registering-the-server-directly),
or use a connection you already configured. The skills work with a native executable,
Python installation, or [HTTP service](../scenarios/http-service.md) exposing the
mcp-windbg tools. Windows and CDB/KD are required on the server host; an HTTP client
can run elsewhere. uv is needed only if your own server command uses it.

The skills-only plugin adds no server, runtime, agent, or symbol settings. It resolves
its tool calls through your existing MCP connection. If tools are missing, run
`/mcp-windbg-skills:windbg-doctor` to check that connection and its actual launcher.

Both plugins come from the same marketplace and can be installed together.
The uvx plugin works on its own; adding or removing skills does not affect its server.
Restart Claude Code if newly installed skills or tools do not appear.

## Skills

Only `mcp-windbg-skills` provides these workflows, with the same invocation names
regardless of how the server is installed:

| Workflow | Skill |
| --- | --- |
| Triage a crash dump | `/mcp-windbg-skills:analyze-dump` |
| Debug a live user-mode process | `/mcp-windbg-skills:debug-remote` |
| Drive a live kernel target | `/mcp-windbg-skills:kernel-debug` |
| Diagnose the debugging setup | `/mcp-windbg-skills:windbg-doctor` |

For example, with an independently installed server:

```text
/mcp-windbg-skills:analyze-dump C:\dumps\app.dmp
```

The server's built-in [MCP prompts](prompts.md) remain available with or without a
plugin. They are separate from these Claude Code skills.

The uvx bundle also includes **`crash-analyst`**, an agent that investigates a dump
and reports its verdict, evidence, and next steps. Ask for it by name:
*"use the crash-analyst agent on C:\dumps\app.dmp"*. The skills-only plugin does not
include this agent.

## Server options and symbols

The uvx bundle launches the server using its `.mcp.json`, pinned to the matching
release. uvx fetches that version from PyPI on first use and caches it. Updating
the bundle updates the pinned server too.

The bundle supplies `_NT_SYMBOL_PATH` with the Microsoft symbol server and a
`C:\Symbols` cache only when you have not set your own value. To customize it:

```powershell
setx _NT_SYMBOL_PATH "SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols;C:\my\pdbs"
```

Restart the terminal and Claude Code so new processes inherit the value.

For an independently registered server, configure symbols and [command-line options](cli.md)
in that registration. Updating skills does not update the server or its
configuration. Use `--symbols-path` or `_NT_SYMBOL_PATH` on the server host; the
client shell's value may differ, especially over HTTP.

For custom `--cdb-path`, `--kd-path`, `--filter-script`, or timeout settings,
[register the server directly](clients.md#registering-the-server-directly) and add
the skills-only plugin. Edits to the installed uvx bundle are overwritten on update.

## Upgrade from bundled skills

Earlier uvx plugin versions included the four skills. After updating the uvx plugin,
install `mcp-windbg-skills@mcp-windbg` separately if you want to keep them. Change
skill invocations from `/mcp-windbg:` to `/mcp-windbg-skills:`; for example,
`/mcp-windbg:analyze-dump` becomes `/mcp-windbg-skills:analyze-dump`.
Keep the uvx plugin installed to continue using its server and agent.

## Switch to a manually registered server

Finish open debugging sessions, then uninstall the bundle:

```text
/plugin uninstall mcp-windbg-uvx@mcp-windbg
```

[Register your server](clients.md#registering-the-server-directly), install
`mcp-windbg-skills@mcp-windbg` as above, and restart Claude Code. Confirm the server
connection with `/mcp`, then run `/mcp-windbg-skills:windbg-doctor` if you installed
the skills plugin. An existing skills installation can stay in place.

## Updating and removing

For the skills-only plugin:

```text
/plugin marketplace update mcp-windbg
/plugin update mcp-windbg-skills@mcp-windbg
```

For the uvx bundle, substitute `mcp-windbg-uvx@mcp-windbg` in the update command.

To remove the skills-only plugin:

```text
/plugin uninstall mcp-windbg-skills@mcp-windbg
```

Your MCP server, whether supplied by the uvx plugin or registered independently,
remains configured. For the uvx plugin,
uninstall `mcp-windbg-uvx@mcp-windbg` instead; that also removes its server registration.
Remove the marketplace with `/plugin marketplace remove mcp-windbg` when neither
plugin is needed.
