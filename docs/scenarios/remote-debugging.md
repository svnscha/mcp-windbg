# Debug a remote target

Beyond frozen dumps, you can attach to a **live** debugging session and inspect it as it
runs. This is for connecting to a debugging server that is already listening, for example a
`cdb -server` started on the target machine.

## Connect

Give the model the connection string:

```text
Connect to tcp:Port=5005,Server=192.168.0.100
```

This calls [`open_cdb_remote`](../reference/tools.md#open_cdb_remote). Supported
connection string formats:

| Transport | Example |
| --- | --- |
| TCP | `tcp:Port=5005,Server=192.168.0.100` |
| Named pipe | `npipe:Pipe=MyPipe,Server=MyServer` |
| COM | `com:Port=COM1,Baud=115200` |

!!! note "Connecting to a debugging server, not attaching directly"
    `open_cdb_remote` attaches to an existing debugging *server* (`cdb`/WinDbg started with
    `-server`). It does not attach to a running process by PID. Kernel-mode debugging over a
    `-k` cable is a different mode, handled by [Debug a kernel target](kernel-debugging.md). To
    debug a local process, start a `cdb -server` on it first, then connect.

## Start an unattended server safely

!!! warning "NUL or EOF stdin can freeze the Windows host"
    CDB 10.0.29661.1004 with stdin redirected to `NUL` (`/dev/null` in Git Bash,
    `subprocess.DEVNULL` in Python), or a stdin pipe that reaches EOF, can rapidly
    exhaust Windows **nonpaged pool** and freeze the whole host. This is kernel
    memory, so a small process memory footprint or bounded output does not make
    the launch safe. See [the CDB upstream report](https://github.com/microsoft/WinDbg-Feedback/issues/402).

For an unattended `cdb -server`, disable local console I/O:

```text
cdb -server tcp:port=5005 -noio <program>
```

Keep `-server` and its transport first, followed by `-noio`, as required by the
[CDB command-line options](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/cdb-command-line-options).
`-noio` disables the server console input and output; issue commands and read
output through the remote client (`open_cdb_remote` and `run_cdb_command`). An
empty server stdout log is expected in this mode. Apply `-noio` to the external
`-server` process, not to the MCP `-remote` client, which needs its stdin/stdout.
Never use NUL/EOF stdin without `-noio`. An interactive server can instead keep
its real console open.

Before an agent launches a server, it must arrange cleanup for success, failed
connections, and interrupted investigations, then record the spawned server PID. Do not launch
a detached server without a way to stop and verify that specific process. If
`Could not write to pipe, 1450` repeats, stop only the server you started instead
of retrying commands or relying on log rotation. Leave pre-existing servers alone.

## Break in, then inspect

If the target is running, pause it before you inspect state. Ask the model to break in,
which calls [`send_ctrl_break`](../reference/tools.md#send_ctrl_break):

```text
Send CTRL+BREAK to interrupt the target, then show all thread stacks with ~*k
```

Once stopped, investigate the same way you would a dump, through
[`run_cdb_command`](../reference/tools.md#run_cdb_command):

```text
Show the current registers and call stack
List all threads and point out any that look stuck
Check thread CPU time with !runaway
Look for held locks with !locks
```

A typical hang investigation:

```text
1. Connect to tcp:Port=5005,Server=192.168.0.100
2. Send CTRL+BREAK so we can inspect safely
3. Show current state - registers, stack, threads
4. Run ~*k and identify the thread holding things up
5. Run !locks to check synchronization objects
```

For a guided investigation, use the built-in [`remote-triage`](../reference/prompts.md)
prompt, which walks the model through breaking in, orienting, and testing a hypothesis.

## Close the connection when done

```text
Close the connection to tcp:Port=5005,Server=192.168.0.100
```

This calls [`close_cdb_session`](../reference/tools.md#close_cdb_session) and releases
the MCP client session. It does **not** stop the separately launched debug server.
If you started a server for this investigation, stop that specific process and
verify it exited; leave pre-existing servers alone.

## Related

- [Tools reference](../reference/tools.md) - `open_cdb_remote`, `send_ctrl_break`, `run_cdb_command`.
- [Troubleshooting](../troubleshooting.md#remote-debugging-issues) - connection failures.
