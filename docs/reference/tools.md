# Tools

The server exposes these MCP tools. You rarely name them directly, the model picks the right
one from your request, but this is the precise contract for each.

| Tool | Purpose |
| --- | --- |
| [`list_windbg_dumps`](#list_windbg_dumps) | List crash dump files in a directory. |
| [`open_windbg_dump`](#open_windbg_dump) | Open a dump and run the standard triage commands. |
| [`close_windbg_dump`](#close_windbg_dump) | Close a dump session. |
| [`open_windbg_remote`](#open_windbg_remote) | Connect to a user-mode remote debugging session. |
| [`close_windbg_remote`](#close_windbg_remote) | Close a remote session. |
| [`open_windbg_kernel`](#open_windbg_kernel) | Connect to a kernel debugging target. |
| [`close_windbg_kernel`](#close_windbg_kernel) | Close a kernel session. |
| [`run_windbg_cmd`](#run_windbg_cmd) | Run any WinDbg command in an open session. |
| [`send_ctrl_break`](#send_ctrl_break) | Break into a running target. |

Sessions are persistent: opening a dump or remote target keeps a `cdb.exe` process alive so
follow-up commands reuse it. Several can be open at once, each addressed by its dump path or
connection string, so you can compare dumps side by side. Close sessions when you finish to free
resources.

---

## list_windbg_dumps

List crash dump files in a directory.

| Parameter | Required | Description |
| --- | --- | --- |
| `directory_path` | no | Directory to search. Defaults to the configured local crash dump location. |
| `recursive` | no | Search subdirectories as well. Defaults to `false`. |

Used by [Triage multiple dumps](../scenarios/triage.md).

---

## open_windbg_dump

Open a crash dump and run the common analysis commands (`.lastevent`, `!analyze -v`, stack,
modules, threads). Creates a session if one does not already exist for the dump.

| Parameter | Required | Description |
| --- | --- | --- |
| `dump_path` | yes | Path to the `.dmp` file. |
| `include_stack_trace` | yes | Include the stack trace in the analysis. |
| `include_modules` | yes | Include loaded module information. |
| `include_threads` | yes | Include thread information. |
| `symbols_path` | no | Extra symbol search path. Only applied when the session is first created. |

Used by [Analyze a crash dump](../scenarios/crash-dump.md).

---

## close_windbg_dump

Close a dump session and release its `cdb.exe` process.

| Parameter | Required | Description |
| --- | --- | --- |
| `dump_path` | yes | Path of the dump whose session to close. |

---

## open_windbg_remote

Connect to a user-mode remote debugging session (a `cdb`/WinDbg `-server`), launched with
`-remote`. Creates a session if one does not already exist for the connection. For kernel
targets use [`open_windbg_kernel`](#open_windbg_kernel) instead: `-remote` cannot drive a
kernel cable.

| Parameter | Required | Description |
| --- | --- | --- |
| `connection_string` | yes | Remote connection string, see formats below. |
| `include_stack_trace` | no | Include the stack trace. Defaults to `false`. |
| `include_modules` | no | Include loaded module information. Defaults to `false`. |
| `include_threads` | no | Include thread information. Defaults to `false`. |

Connection string formats:

| Transport | Example |
| --- | --- |
| TCP | `tcp:Port=5005,Server=192.168.0.100` |
| Named pipe | `npipe:Pipe=MyPipe,Server=MyServer` |
| COM | `com:Port=COM1,Baud=115200` |

Used by [Debug a remote target](../scenarios/remote-debugging.md).

---

## close_windbg_remote

Close a remote session.

| Parameter | Required | Description |
| --- | --- | --- |
| `connection_string` | yes | Connection string of the session to close. |

---

## open_windbg_kernel

Connect to a kernel debugging target, launched with `-k`. Creates a session if one does not
already exist for the connection. This is a different mechanism from
[`open_windbg_remote`](#open_windbg_remote): kernel debugging (`-k`) and user-mode remote
debugging (`-remote`) are not interchangeable.

| Parameter | Required | Description |
| --- | --- | --- |
| `connection_string` | yes | Kernel connection string, see formats below. |
| `include_stack_trace` | no | Include the stack trace. Defaults to `false`. |
| `include_modules` | no | Include loaded module information. Defaults to `false`. |
| `include_threads` | no | Include thread information. Defaults to `false`. |

Connection string formats:

| Transport | Example |
| --- | --- |
| KDNET (network) | `net:port=50000,key=1.2.3.4` |
| Named pipe (VM) | `com:pipe,port=\\.\pipe\com_1,baud=115200,reconnect,resets=0` |
| Serial | `com:port=COM1,baud=115200` |

Pass the named-pipe path with real single backslashes. Because JSON escapes each backslash,
`\\.\pipe\com_1` is written `"\\\\.\\pipe\\com_1"` in a tool call. Do not add extra
backslashes. Either `cdb.exe` (the default) or `kd.exe` (via `--cdb-path`) can drive `-k`.

Used by [Debug a kernel target](../scenarios/kernel-debugging.md).

---

## close_windbg_kernel

Close a kernel session.

| Parameter | Required | Description |
| --- | --- | --- |
| `connection_string` | yes | Kernel connection string of the session to close. |

---

## run_windbg_cmd

Run any WinDbg command in an open session and return its output. Targets a dump or a remote
session; if none is open for the given target, one is created automatically.

| Parameter | Required | Description |
| --- | --- | --- |
| `command` | yes | The WinDbg command to run, for example `kb` or `!analyze -v`. |
| `dump_path` | one of | Run against this dump's session. |
| `connection_string` | one of | Run against this remote or kernel session. |
| `connection_type` | no | How `connection_string` attaches: `user` (default, `-remote`) or `kernel` (`-k`). Ignored for `dump_path`. |
| `symbols_path` | no | Extra symbol search path. Only applied when the session is first created. |

Provide exactly one of `dump_path` or `connection_string`. To address a kernel session, set
`connection_type` to `kernel`.

---

## send_ctrl_break

Send a CTRL+BREAK to an active session to break into a running target. Useful before
inspecting a live remote session.

| Parameter | Required | Description |
| --- | --- | --- |
| `dump_path` | one of | The dump session to signal. |
| `connection_string` | one of | The remote or kernel session to signal. |
| `connection_type` | no | How `connection_string` attaches: `user` (default, `-remote`) or `kernel` (`-k`). Ignored for `dump_path`. |

Provide exactly one of `dump_path` or `connection_string`. Used by
[Debug a remote target](../scenarios/remote-debugging.md) and
[Debug a kernel target](../scenarios/kernel-debugging.md).

---

## Common WinDbg commands

You can describe what you want instead of memorizing these, but they are handy to know. All
run through [`run_windbg_cmd`](#run_windbg_cmd).

| Area | Commands |
| --- | --- |
| Stack | `k`, `kb`, `kv`, `~*k`, `.ecxr` |
| Memory | `db` / `dw` / `dd` / `dp <address>`, `!address <address>` |
| Heap | `!heap -p -a <address>`, `!heap -stat` |
| Threads | `~`, `~*k`, `!runaway`, `!locks` |
| Modules | `lm`, `lmv`, `!lmi <module>` |
| Analysis | `!analyze -v`, `.lastevent` |
