# Manual feature verification

End-to-end checks for the four debugging capabilities, to run by hand against real
targets. The automated suite (`scenarios/*.yaml`, `test_build_cdb_args.py`) proves the
plumbing, but a real user-mode remote, and especially a real kernel attach, need live
targets a CI runner does not have. Use this checklist to confirm an implementation on a
machine that does.

Each step gives the exact tool call (the JSON a client sends) and what a healthy response
contains. Drive them through any MCP client, or with `run_windbg_cmd` after opening a
session.

| Capability | Automated in CI | Needs a live target |
| --- | --- | --- |
| Crash-dump analysis | yes (`analyze_dump.yaml`) | no - a `.dmp` file |
| User-mode remote (`-remote`) | yes (`remote_debugging.yaml`, local `cdb -server`) | a debug server |
| Kernel debugging (`-k`) | no (arg-builder unit test only) | a VM/second machine |
| Command construction (`-z`/`-remote`/`-k`) | yes (`test_build_cdb_args.py`) | no |

## 1. Crash-dump analysis

```json
{ "tool": "open_windbg_dump",
  "arguments": { "dump_path": "C:\\dumps\\app.dmp",
                 "include_stack_trace": true, "include_modules": false, "include_threads": false } }
```

Expect: a `### Crash Analysis` section with `!analyze -v` output and a `### Stack Trace`
section. Close with `close_windbg_dump`.

## 2. User-mode remote debugging (`-remote`)

Start a debug server on the target first: `cdb -server tcp:port=5005 -g <program>`.

```json
{ "tool": "open_windbg_remote",
  "arguments": { "connection_string": "tcp:Port=5005,Server=192.168.0.100" } }
```

Expect: `### Target Process Information` (from `!peb`) and `### Current Registers`. Break in
with `send_ctrl_break`, inspect with `run_windbg_cmd`, close with `close_windbg_remote`.

## 3. Kernel debugging over KDNET (`-k net:`)

On the target: `bcdedit /debug on` then `bcdedit /dbgsettings net hostip:<host> port:50000`,
and reboot. `bcdedit` prints the key.

```json
{ "tool": "open_windbg_kernel",
  "arguments": { "connection_string": "net:port=50000,key=1.2.3.4" } }
```

Expect: `### Kernel Target Information` (from `vertarget`, showing the target Windows build)
and `### Current Registers`. This is the case issue #62 asked for - confirm the debugger was
launched with `-k net:...`, not `-remote`. Break in with `send_ctrl_break`, run `!analyze -v`
via `run_windbg_cmd` with `"connection_type": "kernel"`, close with `close_windbg_kernel`.

## 4. Kernel debugging over a named pipe (`-k com:pipe`)

Map the VM's first serial port to a host named pipe (Hyper-V "COM 1" -> `\\.\pipe\com_1`).

```json
{ "tool": "open_windbg_kernel",
  "arguments": { "connection_string": "com:pipe,port=\\\\.\\pipe\\com_1,baud=115200,reconnect,resets=0" } }
```

Note the JSON escaping: the real string is `com:pipe,port=\\.\pipe\com_1,baud=115200,...`.
This is the exact case from issue #47. Expect the same sections as KDNET. If it times out,
confirm the pipe exists and the VM is booted with debugging enabled - the string is passed to
`-k` verbatim, so extra backslashes are the usual mistake.

## Driving a kernel session with run_windbg_cmd

Once open, address the kernel session explicitly:

```json
{ "tool": "run_windbg_cmd",
  "arguments": { "connection_string": "net:port=50000,key=1.2.3.4",
                 "connection_type": "kernel", "command": "!process 0 0" } }
```

`connection_type: "kernel"` routes to the `kernel:` session; omitting it (or `"user"`) would
open a separate user-mode `-remote` session against the same string.
