# Debug a kernel target

Kernel debugging attaches to a whole machine (usually a VM) rather than a single process. It
uses a different debugger mode from user-mode remote debugging: the target is reached over a
`-k` cable (KDNET, a named pipe, or a serial line), not a `cdb -server`. This is what you want
for driver bugs, bugchecks, and boot-time issues.

## Prepare the target

Enable kernel debugging on the target machine once, then reboot it. For KDNET over the
network:

```powershell
bcdedit /debug on
bcdedit /dbgsettings net hostip:w.x.y.z port:50000
```

`bcdedit` prints the key to use. For a virtual machine you can instead expose a named pipe
(Hyper-V "COM 1" mapped to `\\.\pipe\com_1`, or the equivalent VMware serial port).

## Connect

Give the model the kernel connection string:

```text
Open a kernel debugging session on net:port=50000,key=1.2.3.4
```

This calls [`open_windbg_kernel`](../reference/tools.md#open_windbg_kernel). Supported
connection string formats:

| Transport | Example |
| --- | --- |
| KDNET (network) | `net:port=50000,key=1.2.3.4` |
| Named pipe (VM) | `com:pipe,port=\\.\pipe\com_1,baud=115200,reconnect,resets=0` |
| Serial | `com:port=COM1,baud=115200` |

!!! note "Named-pipe backslashes"
    Write the pipe path with real single backslashes: `com:pipe,port=\\.\pipe\com_1,...`. If
    you hand-write the tool call as JSON, each backslash is escaped there, so the path becomes
    `"\\\\.\\pipe\\com_1"`. Do not add extra backslashes.

!!! note "cdb or kd"
    The default `cdb.exe` drives `-k` fine. If you prefer `kd.exe`, point `--cdb-path` at it
    (see [Command-line options](../reference/cli.md)).

## Break in, then inspect

If the target is running, pause it before you inspect state. Ask the model to break in, which
calls [`send_ctrl_break`](../reference/tools.md#send_ctrl_break):

```text
Send CTRL+BREAK to the kernel target, then show the target version with vertarget
```

Once stopped, investigate through [`run_windbg_cmd`](../reference/tools.md#run_windbg_cmd)
(which addresses the kernel session when asked about it):

```text
Show the current bugcheck analysis with !analyze -v
List the loaded drivers with lm
Show the stack of every processor with !running
```

A typical bugcheck investigation:

```text
1. Open a kernel debugging session on net:port=50000,key=1.2.3.4
2. Send CTRL+BREAK so we can inspect safely
3. Run !analyze -v and summarize the bugcheck
4. Show the faulting driver and its stack
5. List loaded modules and flag anything unsigned or unexpected
```

## Close the connection when done

```text
Close the kernel debugging session on net:port=50000,key=1.2.3.4
```

This calls [`close_windbg_kernel`](../reference/tools.md#close_windbg_kernel) and releases the
session.

## Related

- [Tools reference](../reference/tools.md) - `open_windbg_kernel`, `send_ctrl_break`, `run_windbg_cmd`.
- [Debug a remote target](remote-debugging.md) - user-mode remote debugging (`-remote`).
