# Use cases

People use `mcp-windbg` for a handful of jobs. They share the same setup, you just ask for
different work. Pick the one that matches what you have:

| Use case | You have | Key tools |
| --- | --- | --- |
| **[Analyze a crash dump](crash-dump.md)** | A `.dmp` file from a crash. | `open_cdb_dump`, `run_cdb_command`, `close_cdb_session` |
| **[Debug a remote target](remote-debugging.md)** | A live user-mode debugging session to connect to. | `open_cdb_remote`, `send_ctrl_break`, `run_cdb_command` |
| **[Debug a kernel target](kernel-debugging.md)** | A kernel debug connection (KDNET, pipe, serial). | `open_kd_session`, `send_ctrl_break`, `run_kd_command` |
| **[Triage multiple dumps](triage.md)** | A folder full of dumps. | `list_dumps`, then the crash-dump flow per file |
| **[Debug from another machine](http-service.md)** | A Windows debugging host, but you work elsewhere. | Any tool, over the HTTP transport |
| **[Redact sensitive data](redaction.md)** | Dumps with secrets or PII. | A `--filter-script` over any tool |

!!! tip "Dumps vs live targets"
    A **dump** is a frozen snapshot, you read it. A **remote target** is a running session,
    you can break in and inspect it as it executes.

Every tool is described in the **[Tools reference](../reference/tools.md)**, and every
command-line flag in **[Command-line options](../reference/cli.md)**.
