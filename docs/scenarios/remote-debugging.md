# Debug a remote target

Beyond frozen dumps, you can attach to a **live** debugging session and inspect it as it
runs. This is for connecting to a debugging server that is already listening, for example a
`cdb -server` started on the target machine.

## Connect

Give the model the connection string:

```text
Connect to tcp:Port=5005,Server=192.168.0.100
```

This calls [`open_windbg_remote`](../reference/tools.md#open_windbg_remote). Supported
connection string formats:

| Transport | Example |
| --- | --- |
| TCP | `tcp:Port=5005,Server=192.168.0.100` |
| Named pipe | `npipe:Pipe=MyPipe,Server=MyServer` |
| COM | `com:Port=COM1,Baud=115200` |

!!! note "Connecting to a debugging server, not kernel debugging"
    `open_windbg_remote` attaches to an existing debugging *server* (`cdb`/WinDbg started
    with `-server`). Kernel-mode debugging over a `-k` cable is a different mode and is not
    supported yet.

## Break in, then inspect

If the target is running, pause it before you inspect state. Ask the model to break in,
which calls [`send_ctrl_break`](../reference/tools.md#send_ctrl_break):

```text
Send CTRL+BREAK to interrupt the target, then show all thread stacks with ~*k
```

Once stopped, investigate the same way you would a dump, through
[`run_windbg_cmd`](../reference/tools.md#run_windbg_cmd):

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

## Close the connection when done

```text
Close the connection to tcp:Port=5005,Server=192.168.0.100
```

This calls [`close_windbg_remote`](../reference/tools.md#close_windbg_remote) and releases
the session.

## Related

- [Tools reference](../reference/tools.md) - `open_windbg_remote`, `send_ctrl_break`, `run_windbg_cmd`.
- [Troubleshooting](../troubleshooting.md#remote-debugging-issues) - connection failures.
