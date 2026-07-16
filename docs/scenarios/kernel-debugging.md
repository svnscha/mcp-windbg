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

This calls [`open_kd_session`](../reference/tools.md#open_kd_session). Supported
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

!!! note "kd.exe drives kernel sessions"
    Kernel sessions run under `kd.exe` (auto-detected in the same Windows Kits / Microsoft
    Store locations as `cdb.exe`, or set with [`--kd-path`](../reference/cli.md)); `cdb.exe`
    cannot drive a kernel cable.

!!! note "no_debuggee timeout"
    If `open_kd_session` times out mentioning `no_debuggee`, the target is not transmitting -
    it is not booted with debugging enabled, or another debugger already holds the connection
    (KDNET is point-to-point, one debugger at a time). That is an environment issue, not a tool
    failure.

## Inspect the target

`open_kd_session` waits for the target to connect and **breaks in for you**, so it comes back
already stopped at a prompt. There is no separate break-in step: start inspecting straight
away through [`run_kd_command`](../reference/tools.md#run_kd_command), which addresses the
kernel session when asked about it.

```text
Show the current bugcheck analysis with !analyze -v
List the loaded drivers with lm
Show the stack of every processor with !running
```

A typical bugcheck investigation:

```text
1. Open a kernel debugging session on net:port=50000,key=1.2.3.4
2. Run !analyze -v and summarize the bugcheck
3. Show the faulting driver and its stack
4. List loaded modules and flag anything unsigned or unexpected
```

!!! tip "Let it run, then re-halt it"
    You only need [`send_ctrl_break`](../reference/tools.md#send_ctrl_break) to stop a target
    you deliberately let run again (with `g`), for example to catch it in the act. A freshly
    opened session is already halted.

For a guided investigation, use the built-in [`kernel-triage`](../reference/prompts.md)
prompt, which walks the model through orienting, testing a hypothesis, and releasing the
machine at the end.

## Close the connection when done

```text
Close the kernel debugging session on net:port=50000,key=1.2.3.4
```

This calls [`close_kd_session`](../reference/tools.md#close_kd_session), which by default
**resumes the machine** (sends `g`) and releases the session - a kernel target left halted at
a break freezes the whole machine. Pass `resume: false` only if you deliberately want to leave
it stopped.

## Related

- [Tools reference](../reference/tools.md) - `open_kd_session`, `send_ctrl_break`, `run_kd_command`.
- [Debug a remote target](remote-debugging.md) - user-mode remote debugging (`-remote`).
