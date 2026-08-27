---
name: windbg-doctor
description: Check that this machine can actually debug - CDB present, uv available, symbols configured - and explain how to fix whatever is missing. Use when mcp-windbg tools fail, when a session will not open, or before a first debugging session.
---

# Check the debugging setup

Diagnose why mcp-windbg is not working, or confirm it will before someone
relies on it. Report findings together at the end, not one command at a time.

## Checks

Run these with Bash and interpret the results.

**1. Platform.** `uname -s` or `$env:OS`. The server drives `cdb.exe`/`kd.exe`
and is Windows-only. On anything else, stop here and say so - nothing later will
help.

**2. CDB.** Look in the places the server itself searches:

```
C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe
%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX64.exe
```

Missing means WinDbg is not installed. Point at
[aka.ms/windbg](https://aka.ms/windbg) (Microsoft Store) or the Windows SDK's
Debugging Tools for Windows. If it is installed somewhere unusual, the server
takes `--cdb-path` / `--kd-path`.

**3. uv.** `uv --version`. The plugin launches the server with `uvx`, so a
missing uv means the MCP server never starts and every tool fails at once.
`winget install astral-sh.uv`.

**4. The server itself.** `uvx mcp-windbg --help`. This proves the whole chain -
uv resolves the package from PyPI and the entry point runs. A failure here with
uv present usually means no network, or a proxy blocking PyPI.

**5. Symbols.** Check `_NT_SYMBOL_PATH`. The plugin defaults it to the Microsoft
symbol server, so an empty value in the *shell* is not a problem by itself -
what matters is what the server received. Report the effective value and note
that without symbols, stacks resolve only to `module+0x1234` and any analysis
built on them is guesswork.

**6. Tools reachable.** If the MCP server is up, `list_dumps` returning anything
at all - including "no dumps found" - proves the round trip works.

## Reporting

A short table: check, result, and what to do about it. Lead with the first thing
that is actually broken, since later checks often fail only as a consequence of
it. If everything passes, say so in one line and name the CDB path and effective
symbol path you found, so the user knows what they are running against.
