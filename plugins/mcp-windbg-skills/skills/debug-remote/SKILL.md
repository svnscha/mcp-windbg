---
name: debug-remote
description: Attach to a running Windows process through a WinDbg debug server and inspect it live. Use when the user wants to debug a process on another machine, or one already under a .server session, rather than a crash dump.
---

# Debug a live user-mode process

## MCP connection

Use the existing mcp-windbg MCP connection, whether it is launched by a plugin,
a native executable, Python, or an HTTP service. Tool names below are base
names; resolve them against the tools exposed by that connection rather than
assuming a plugin-specific prefix. Keep each session on the server that opened
it. If multiple servers match, use the user's selected server or ask which one.
If the required tools are unavailable, report the missing connection or tool
and help check its configuration; do not register a second server.

Attach to an existing WinDbg/CDB **debug server** with the `mcp-windbg` tools.
This is user-mode only - for a kernel target use the available `kernel-debug` skill.

## The other end

Someone must already be hosting the target. In WinDbg or CDB on that machine:

```
.server tcp:port=5005
```

If they have not, say so rather than guessing a connection string - there is
nothing to attach to yet.

## Safety before starting a server

CDB 10.0.29661.1004 with stdin redirected to `NUL` (`/dev/null` or
`subprocess.DEVNULL`), or a stdin pipe that reaches EOF, can exhaust Windows nonpaged
pool and freeze the host. For unattended startup use:

```text
cdb -server tcp:port=5005 -noio <program>
```

Keep `-server` and its transport first, followed by `-noio`. This disables local
console I/O; use the remote client for commands and output. Apply `-noio` only to
the external `-server` process, never to the MCP-managed -remote client. Never use
NUL/EOF stdin without `-noio`. Arrange cleanup before launching, including failed
connections and interrupted investigations; record the spawned server PID. If
`Could not write to pipe, 1450` repeats, stop only your owned server instead of
retrying. See [the CDB upstream report](https://github.com/microsoft/WinDbg-Feedback/issues/402).

## Connecting

`open_cdb_remote` with the connection string:

- TCP: `tcp:Port=5005,Server=hostname`
- Named pipe: `npipe:Pipe=pipename,Server=hostname`

The target is running when you attach, so the session may not be at a prompt.
`send_ctrl_break` halts it when you need it stopped.

## Working the target

`run_cdb_command` with the `session_id`:

- `~*k` for every thread's stack - the usual first move on a hang
- `!locks`, `!cs -l` for lock contention
- `lm`, `!peb` for what is loaded and how it started
- `bp`, `g`, `wait_for_break` to catch a code path in the act
- `.dump /ma <path>` to capture a dump for offline analysis

## Letting it run again

Attaching to a live process freezes it while broken in, and a frozen process is
usually worse than an unanalyzed one:

- `g` resumes and returns immediately; the target produces no output until it
  stops again.
- `wait_for_break` blocks until it stops on its own.
- Any ordinary command breaks in automatically first.

`close_cdb_session` when finished. Say plainly whether the target was left
running or halted, so nobody discovers a frozen process an hour later. Closing
the session closes only the MCP client, not the external debug server. Separately
stop any server you started and verify it exited; leave pre-existing servers alone.

## Reporting

For a hang, the answer is usually the relationship between threads, not a single
stack: which thread holds what, and which are waiting on it. Say that explicitly
rather than pasting `~*k` and leaving the reader to work it out.
