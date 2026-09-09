---
name: windbg-doctor
description: Check that this machine can actually debug - MCP connection reachable, debugger present, symbols configured - and explain how to fix whatever is missing. Use when mcp-windbg tools fail, when a session will not open, or before a first debugging session.
---

# Check the debugging setup

## MCP connection

Use the existing mcp-windbg MCP connection, whether it is launched by a plugin,
a native executable, Python, or an HTTP service. Tool names below are base
names; resolve them against the tools exposed by that connection rather than
assuming a plugin-specific prefix. Keep each session on the server that opened
it. If multiple servers match, use the user's selected server or ask which one.
If the required tools are unavailable, report the missing connection or tool
and help check its configuration; do not register a second server.

Diagnose why mcp-windbg is not working, or confirm it will before someone
relies on it. Report findings together at the end, not one command at a time.

## Checks

Inspect the configured MCP transport and launch command first. Run host checks
on the machine running the server, using its available shell. For an HTTP
service, local client checks do not describe the server; use server diagnostics
or ask its operator for evidence and mark unavailable checks as unverified.

**1. Tools reachable.** Call `list_dumps` through the configured connection.
A successful response, including an empty list, proves the MCP round trip works;
it does not prove that CDB or KD can start. If connection fails, inspect the
client's MCP status and server logs.

**2. Platform and debugger.** The server host must be Windows. Check configured
`--cdb-path` / `--kd-path` overrides and the debugger locations the server searches:

```text
C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe
%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX64.exe
```

Check KD as well for kernel debugging. If the debugger is missing, point at
[aka.ms/windbg](https://aka.ms/windbg) or the Windows SDK's Debugging Tools for
Windows. Do not infer that the debugger is missing from a client-side path check.

**3. Launcher.** Check only the runtime used by the configured server command:

- `uvx`: check `uv --version` and run the configured package spec with `--help`,
  preserving its version pin. Resolution failures can indicate network or proxy
  problems. uv is required only for this launch method.
- Python or a console entry point: use the configured interpreter or executable
  with `--help` (for example `python -m mcp_windbg --help`).
- Native executable: check that exact executable with `--help`; do not require
  Python or uv.
- HTTP: check the configured endpoint and server logs; do not launch a local
  replacement server.

**4. Symbols.** Inspect `--symbols-path`, the server's `_NT_SYMBOL_PATH`, and,
if a debugger session is already open, `.sympath`. The uvx plugin supplies a
default symbol path; independently configured servers may not. An empty client
shell variable does not establish the server's effective value. Report unknown
values as unverified. Missing symbols limit what stack offsets can tell you.

## Reporting

A short table: check, result, and what to do about it. Lead with the first thing
that is actually broken, since later checks often fail only as a consequence of
it. If everything passes, say so in one line and name the CDB path and effective
symbol path you found, so the user knows what they are running against.
