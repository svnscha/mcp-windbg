# mcp-windbg uvx plugin

Windows crash dump analysis and live WinDbg debugging, inside Claude Code.

> [!NOTE]
> **Enterprise environments:** managed `allowedMcpServers` settings can cause Claude Code to
> silently skip this plugin's MCP server ([issue #32882](https://github.com/anthropics/claude-code/issues/32882)).
> I recommend [manual server installation and registration](#not-using-uv) plus the
> optional [skills](#skills-for-an-existing-server) or [agents](#agents-for-an-existing-server) plugin, subject to your organization's MCP policy.

```
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg-uvx@mcp-windbg
```

That is the whole installation. There is no `pip install` step and no MCP config
to edit - the plugin launches the server with `uvx`, which fetches the pinned
version from PyPI on first use.

[Skills](#skills-for-an-existing-server) and [agents](#agents-for-an-existing-server)
are optional separate installs, for this plugin's server or a connection you configured yourself.

## What you get

Ten tools for driving `cdb.exe` and `kd.exe`:

| | |
| :-- | :-- |
| `list_dumps` | find crash dumps in a directory |
| `open_cdb_dump` | open a `.dmp` and triage it |
| `open_cdb_remote` | attach to a user-mode debug server |
| `open_kd_session` | attach to a kernel target (KDNET, pipe, serial) |
| `run_cdb_command` / `run_kd_command` | run any debugger command |
| `send_ctrl_break` / `wait_for_break` | halt a running target, or wait for it to stop |
| `close_cdb_session` / `close_kd_session` | release the target |

## Requirements

- **Windows.** The server drives `cdb.exe`/`kd.exe`, which are Windows-only.
  There is no way to express that in a plugin manifest, so it is stated here:
  installing on macOS or Linux will load the plugin, and the server will fail to
  find a debugger.
- **CDB.** Install [WinDbg](https://aka.ms/windbg) from the Microsoft Store, or
  the Windows SDK's Debugging Tools for Windows. The server finds it in the
  usual locations; override with `--cdb-path` / `--kd-path` if yours is
  elsewhere.
- **[uv](https://docs.astral.sh/uv/)**, which provides `uvx`. This is the one thing you must
  install yourself; everything else the plugin handles. `winget install astral-sh.uv`, or see
  [Not using uv](#not-using-uv) for the alternative.

## Symbols

Symbols are configured out of the box: if you have no `_NT_SYMBOL_PATH`, the plugin points the
debugger at the Microsoft symbol server and caches to `C:\Symbols`.

If you already have `_NT_SYMBOL_PATH` set, **yours wins** - the plugin only supplies a default,
so a symbol path you tuned yourself is never overwritten.

To use your own, set it in your environment before starting Claude Code:

```powershell
setx _NT_SYMBOL_PATH "SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols;C:\my\pdbs"
```

Restart the terminal afterwards - `setx` only affects new processes. To check what the server
actually received, run `/mcp` in Claude Code and inspect the `mcp-windbg` server.

Without symbols a stack is a list of `module+0x1234` offsets and triage is guesswork, which is
why this defaults to something that works rather than to nothing.

## Not using uv

To control the server's installation and version yourself, register it directly:

```powershell
pip install mcp-windbg
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- python -m mcp_windbg
```

## Skills for an existing server

After installing this uvx plugin or registering mcp-windbg yourself, optionally add the four skills:

```text
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg-skills@mcp-windbg
```

Invoke `/mcp-windbg-skills:analyze-dump`, `/mcp-windbg-skills:debug-remote`,
`/mcp-windbg-skills:kernel-debug`, or `/mcp-windbg-skills:windbg-doctor`.
This plugin uses your configured MCP connection and adds no server, runtime,
symbol settings, or `crash-analyst` agent. It works with a native executable,
Python installation, or HTTP service exposing the mcp-windbg tools.
Install both plugins for the uvx server and skills together. The uvx plugin works
without skills, and uninstalling the skills plugin leaves its server in place.
When upgrading from bundled skills, install this plugin to keep the workflows;
their invocation prefix changes from `/mcp-windbg:` to `/mcp-windbg-skills:`.
The server's built-in MCP prompts remain available independently of the optional plugins.
Updating this plugin updates only the skills; update your server separately.
See the [plugin guide](https://svnscha.github.io/mcp-windbg/reference/plugin/)
for switching from the uvx bundle, updating, and removing plugins.

## Agents for an existing server

```text
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg-agents@mcp-windbg
```

Ask: *"Use the mcp-windbg-agents:crash-analyst agent on C:\dumps\app.dmp"*.
It investigates the dump through your existing MCP connection and reports its
verdict, evidence, and next steps. The skills plugin is not required. Install
this plugin separately when upgrading from a version that bundled the agent.

## Links

- [Documentation](https://svnscha.github.io/mcp-windbg/)
- [Tool reference](https://svnscha.github.io/mcp-windbg/reference/tools/)
- [Issues](https://github.com/svnscha/mcp-windbg/issues)
