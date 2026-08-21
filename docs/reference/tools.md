# Tools

The server exposes these MCP tools. You rarely name them directly, the model picks the right
one from your request, but this is the precise contract for each.

| Tool | Purpose |
| --- | --- |
| [`list_dumps`](#list_dumps) | List crash dump files in a directory. |
| [`open_cdb_dump`](#open_cdb_dump) | Open a dump and run the standard triage commands (`cdb.exe`). |
| [`open_cdb_remote`](#open_cdb_remote) | Attach to a user-mode remote debug server (`-remote`). |
| [`open_kd_session`](#open_kd_session) | Attach to a kernel debugging target (`-k`, `kd.exe`). |
| [`run_cdb_command`](#run_cdb_command) | Run a command on a user-mode (cdb) session. |
| [`run_kd_command`](#run_kd_command) | Run a command on a kernel (kd) session. |
| [`close_cdb_session`](#close_cdb_session) | Close a user-mode session. |
| [`close_kd_session`](#close_kd_session) | Close a kernel session. |
| [`send_ctrl_break`](#send_ctrl_break) | Break into a running target. |
| [`wait_for_break`](#wait_for_break) | Wait for a resumed target to stop. |

## Sessions and session ids

Every `open_*` tool starts a debugger process and returns an opaque **`session_id`** on the
first line of its output, for example:

```
session_id: cdb-1a2b3c4d
```

Pass that id to every follow-up call for the session - `run_*`, `close_*`, `send_ctrl_break`,
and `wait_for_break`. Sessions are persistent (the `cdb.exe`/`kd.exe` process stays alive between
calls), and several can be open at once, so you can compare dumps side by side. Close sessions
when you finish to free resources.

User-mode targets (dumps and `-remote`) run under **`cdb.exe`** and use the `cdb` tools; kernel
targets run under **`kd.exe`** and use the `kd` tools. The id itself is prefixed (`cdb-…` /
`kd-…`), and the server rejects a mismatch - calling [`run_kd_command`](#run_kd_command) with a
`cdb` id returns a tool error telling you which tool to use.

## Timeouts

Each `open_*` / `run_*` call accepts an optional `timeout_seconds` to override the default for
that call. Defaults: `open_cdb_dump` 180s (it runs `!analyze -v`), connects 60s,
`run_cdb_command` 60s, `run_kd_command` 120s (kernel memory reads over KDNET can be slow). The
server-wide [`--timeout`](cli.md) is a floor for these. On a **live** session (remote or
kernel) a command that outruns its timeout is broken into with CTRL+BREAK and the session is
resynchronized, so a slow command reports a timeout instead of wedging the session.

---

## list_dumps

List crash dump files in a directory.

| Parameter | Required | Description |
| --- | --- | --- |
| `directory_path` | no | Directory to search. Defaults to the configured local crash dump location. |
| `recursive` | no | Search subdirectories as well. Defaults to `false`. |

Used by [Triage multiple dumps](../scenarios/triage.md).

---

## open_cdb_dump

Open a crash dump and run the common analysis commands (`.lastevent`, `!analyze -v`, and
optionally stack, modules, threads). Returns a `session_id`.

| Parameter | Required | Description |
| --- | --- | --- |
| `dump_path` | yes | Path to the `.dmp` file. |
| `include_stack_trace` | no | Include the stack trace (`kb`). Defaults to `false`. |
| `include_modules` | no | Include loaded module information (`lm`). Defaults to `false`. |
| `include_threads` | no | Include thread information (`~`). Defaults to `false`. |
| `symbols_path` | no | Extra symbol search path. |
| `timeout_seconds` | no | Override the open/analysis timeout (default 180s). |

Used by [Analyze a crash dump](../scenarios/crash-dump.md).

---

## open_cdb_remote

Attach to a user-mode remote debug server (a `cdb`/WinDbg `-server`), launched with `-remote`.
Returns a `session_id`. For kernel targets use [`open_kd_session`](#open_kd_session) instead:
`-remote` cannot drive a kernel cable.

| Parameter | Required | Description |
| --- | --- | --- |
| `connection_string` | yes | Remote connection string, see formats below. |
| `include_stack_trace` | no | Include the stack trace. Defaults to `false`. |
| `include_modules` | no | Include loaded module information. Defaults to `false`. |
| `include_threads` | no | Include thread information. Defaults to `false`. |
| `symbols_path` | no | Extra symbol search path. |
| `timeout_seconds` | no | Override the connect timeout (default 60s). |

Connection string formats:

| Transport | Example |
| --- | --- |
| TCP | `tcp:Port=5005,Server=192.168.0.100` |
| Named pipe | `npipe:Pipe=MyPipe,Server=MyServer` |
| COM | `com:Port=COM1,Baud=115200` |

Used by [Debug a remote target](../scenarios/remote-debugging.md).

---

## open_kd_session

Attach to a kernel debugging target, launched with `-k` using `kd.exe`. Waits for the target to
connect, breaks in, and returns a `session_id`. This is a different mechanism from
[`open_cdb_remote`](#open_cdb_remote): kernel debugging (`-k`) and user-mode remote debugging
(`-remote`) are not interchangeable.

| Parameter | Required | Description |
| --- | --- | --- |
| `connection_string` | yes | Kernel connection string, see formats below. |
| `include_stack_trace` | no | Include the stack trace. Defaults to `false`. |
| `include_modules` | no | Include loaded module information. Defaults to `false`. |
| `include_threads` | no | Include thread information. Defaults to `false`. |
| `symbols_path` | no | Extra symbol search path. |
| `timeout_seconds` | no | Override the connect/break-in timeout (default 60s). |

Connection string formats:

| Transport | Example |
| --- | --- |
| KDNET (network) | `net:port=50000,key=1.2.3.4` |
| Named pipe (VM) | `com:pipe,port=\\.\pipe\com_1,baud=115200,reconnect,resets=0` |
| Serial | `com:port=COM1,baud=115200` |

Pass the named-pipe path with real single backslashes. Because JSON escapes each backslash,
`\\.\pipe\com_1` is written `"\\\\.\\pipe\\com_1"` in a tool call. Do not add extra backslashes.

A timeout mentioning `no_debuggee` means the target is not transmitting on the transport (not
booted with debugging enabled, or another debugger already holds the connection - KDNET is
point-to-point). That is an environment issue, not a tool failure.

Used by [Debug a kernel target](../scenarios/kernel-debugging.md).

---

## run_cdb_command

Run any WinDbg command on an open user-mode (cdb) session and return its output.

| Parameter | Required | Description |
| --- | --- | --- |
| `session_id` | yes | A `cdb` session id from [`open_cdb_dump`](#open_cdb_dump) or [`open_cdb_remote`](#open_cdb_remote). |
| `command` | yes | The command to run, for example `kb` or `!analyze -v`. |
| `timeout_seconds` | no | Override the command timeout (default 60s). |

---

## run_kd_command

Run any command on an open kernel (kd) session and return its output.

| Parameter | Required | Description |
| --- | --- | --- |
| `session_id` | yes | A `kd` session id from [`open_kd_session`](#open_kd_session). |
| `command` | yes | The command to run, for example `!process 0 0` or `vertarget`. |
| `timeout_seconds` | no | Override the command timeout (default 120s). |

---

## close_cdb_session

Close a user-mode session and release its `cdb.exe` process.

| Parameter | Required | Description |
| --- | --- | --- |
| `session_id` | yes | The `cdb` session id to close. |

---

## close_kd_session

Close a kernel session and release its `kd.exe` process. By default this **resumes the target
machine** (sends `g`) so it runs again - a kernel target left halted at a break freezes the
whole machine. Always close a kernel session when done.

| Parameter | Required | Description |
| --- | --- | --- |
| `session_id` | yes | The `kd` session id to close. |
| `resume` | no | Resume the machine on close. Defaults to `true`. Set `false` to intentionally leave it halted at the break (it stays frozen until a debugger resumes it). |

---

## send_ctrl_break

Send a CTRL+BREAK to a live session (remote or kernel) to break into a running target. Useful
before inspecting a running remote session, or to halt a kernel target.

| Parameter | Required | Description |
| --- | --- | --- |
| `session_id` | yes | A live session id (cdb remote or kd) to break into. |

A dump session has no running target, so this returns an error for `cdb` dump ids. Used by
[Debug a remote target](../scenarios/remote-debugging.md) and
[Debug a kernel target](../scenarios/kernel-debugging.md).

---

## wait_for_break

Block until a target you resumed stops again - on a bugcheck, a breakpoint, or a CTRL+BREAK from
elsewhere - and return everything the debugger printed when it did.

| Parameter | Required | Description |
| --- | --- | --- |
| `session_id` | yes | A live session id (cdb remote or kd) whose target is running. |
| `timeout_seconds` | no | How long to wait. Defaults to 300. |

It asks the debugger rather than trusting a flag, so it is right about a target that is running
for reasons this server never caused - one resumed from the machine's own console, say. If the
target is already stopped it returns immediately. If the wait expires the target is **left
running**: wait again, or halt it with [`send_ctrl_break`](#send_ctrl_break).

---

## Letting the target run

`g` and its relatives (`gh`, `gn`, `gN`, `gc`, `gu`) are different from every other command: they
hand the CPU back to the target, and the debugger prints nothing more until it stops. Sent through
[`run_cdb_command`](#run_cdb_command) or [`run_kd_command`](#run_kd_command) on a live session they
return straight away, leaving the target running - they do not wait for output, and they do not
count against the command timeout.

From there you have three options:

- [`wait_for_break`](#wait_for_break) - block until the target stops, and collect the bugcheck or
  breakpoint report.
- [`send_ctrl_break`](#send_ctrl_break) - halt it now.
- Just send another command. The server breaks in for you first and puts whatever the target
  printed on the way to stopping - the bugcheck banner, the breakpoint report - at the head of
  that command's output, so the reason it stopped is never lost.

Qualified and compound forms count too. `~0 g` and `~*g` resume the target, and in
`bp nt!NtCreateFile; g` the breakpoint runs first with its result returned to you - so a typo'd
symbol shows up as `Couldn't resolve error` instead of a wait for a break that can never come.
A `;` inside a quoted command string (`bp X ".echo hit; g"`) belongs to the breakpoint, not to
you, and is left alone.

Stepping commands (`p`, `t`, `pa`, `ta`, ...) are *not* in this family: they run a single
instruction or line and come back to the prompt, so they behave like any other command.

A second `g` while the target really is still running is refused rather than queued - it would
have resumed the target again the instant it stopped.

While [`wait_for_break`](#wait_for_break) is parked on a session, other calls on that **same**
session are refused immediately with a message pointing at
[`send_ctrl_break`](#send_ctrl_break); other sessions keep working normally, and closing the
session ends the wait.

---

## Common WinDbg commands

You can describe what you want instead of memorizing these, but they are handy to know. Run them
through [`run_cdb_command`](#run_cdb_command) (user mode) or [`run_kd_command`](#run_kd_command)
(kernel).

| Area | Commands |
| --- | --- |
| Stack | `k`, `kb`, `kv`, `~*k`, `.ecxr` |
| Memory | `db` / `dw` / `dd` / `dp <address>`, `!address <address>` |
| Heap | `!heap -p -a <address>`, `!heap -stat` |
| Threads | `~`, `~*k`, `!runaway`, `!locks` |
| Modules | `lm`, `lmv`, `!lmi <module>` |
| Analysis | `!analyze -v`, `.lastevent` |
| Kernel | `!process 0 0`, `!thread`, `vertarget`, `!pcr`, `lm m nt` |
