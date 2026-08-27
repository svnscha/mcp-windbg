# mcp-windbg plugin

Windows crash dump analysis and live WinDbg debugging, inside Claude Code.

```
/plugin marketplace add svnscha/mcp-windbg
/plugin install mcp-windbg@mcp-windbg
```

That is the whole installation. There is no `pip install` step and no MCP config
to edit - the plugin launches the server with `uvx`, which fetches the pinned
version from PyPI on first use.

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

Four skills:

- **`/mcp-windbg:analyze-dump`** - triage a crash dump: exception, faulting frame,
  and what the evidence does and does not support.
- **`/mcp-windbg:debug-remote`** - attach to a running process through a WinDbg
  debug server and work out a hang or a live fault.
- **`/mcp-windbg:kernel-debug`** - drive a live kernel target, including resuming
  the machine and waiting for a bugcheck or breakpoint.
- **`/mcp-windbg:windbg-doctor`** - check that this machine can debug at all
  (CDB, uv, symbols) and explain what to fix. Start here when something fails.

And an agent:

- **`crash-analyst`** - hand it a dump and a question and it investigates on its
  own, then reports a verdict with the evidence behind it, what it ruled out, and
  what to do next. Worth using when a dump needs more than a first look, or when
  you want the analysis kept out of your main conversation.

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

The plugin pins the server version so the plugin and the server it drives stay
in lockstep. To run it a different way, install the package yourself and point
the config at it - edit `.mcp.json` in the installed plugin, or skip the plugin
and register the server directly:

```powershell
pip install mcp-windbg
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- python -m mcp_windbg
```

You lose the bundled skills that way, but the tools are identical.

## Links

- [Documentation](https://svnscha.github.io/mcp-windbg/)
- [Tool reference](https://svnscha.github.io/mcp-windbg/reference/tools/)
- [Issues](https://github.com/svnscha/mcp-windbg/issues)
