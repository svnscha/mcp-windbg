# Tools

The server exposes these MCP tools. You rarely name them directly, the model picks the right
one from your request, but this is the precise contract for each.

| Tool | Purpose |
| --- | --- |
| [`list_windbg_dumps`](#list_windbg_dumps) | List crash dump files in a directory. |
| [`open_windbg_dump`](#open_windbg_dump) | Open a dump and run the standard triage commands. |
| [`close_windbg_dump`](#close_windbg_dump) | Close a dump session. |
| [`open_windbg_remote`](#open_windbg_remote) | Connect to a live remote debugging session. |
| [`close_windbg_remote`](#close_windbg_remote) | Close a remote session. |
| [`run_windbg_cmd`](#run_windbg_cmd) | Run any WinDbg command in an open session. |
| [`send_ctrl_break`](#send_ctrl_break) | Break into a running target. |

Sessions are persistent: opening a dump or remote target keeps a `cdb.exe` process alive so
follow-up commands reuse it. Close sessions when you finish to free resources.

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

Connect to a live remote debugging session (a `cdb`/WinDbg `-server`). Creates a session if
one does not already exist for the connection.

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

## run_windbg_cmd

Run any WinDbg command in an open session and return its output. Targets a dump or a remote
session; if none is open for the given target, one is created automatically.

| Parameter | Required | Description |
| --- | --- | --- |
| `command` | yes | The WinDbg command to run, for example `kb` or `!analyze -v`. |
| `dump_path` | one of | Run against this dump's session. |
| `connection_string` | one of | Run against this remote session. |
| `symbols_path` | no | Extra symbol search path. Only applied when the session is first created. |

Provide exactly one of `dump_path` or `connection_string`.

---

## send_ctrl_break

Send a CTRL+BREAK to an active session to break into a running target. Useful before
inspecting a live remote session.

| Parameter | Required | Description |
| --- | --- | --- |
| `dump_path` | one of | The dump session to signal. |
| `connection_string` | one of | The remote session to signal. |

Provide exactly one of `dump_path` or `connection_string`. Used by
[Debug a remote target](../scenarios/remote-debugging.md).

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
