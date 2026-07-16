# Prompts

The server exposes three MCP prompts, one per debugging mode. Prompts are reusable
instructions your client can insert into the conversation; most clients surface them as a
slash command or a prompt picker. Selecting one is optional, the [tools](tools.md) work
without it.

In VS Code a prompt appears as a slash command (for example `/mcp.mcp-windbg.dump-triage`);
other clients list them in a prompt menu. Each takes one optional argument: pass it and the
model starts on that target, omit it and the model asks.

| Prompt | For | Argument |
| --- | --- | --- |
| [`dump-triage`](#dump-triage) | A crash dump | `dump_path` |
| [`remote-triage`](#remote-triage) | A live user-mode target | `connection_string` |
| [`kernel-triage`](#kernel-triage) | A live kernel target | `connection_string` |

## dump-triage

A comprehensive single-dump triage workflow. It walks the model through opening a
dump, extracting metadata (`vertarget`, `lm`, `k`, `.time`, `!peb`, `r`), closing
the session, and writing a structured crash report.

| Argument | Required | Description |
| --- | --- | --- |
| `dump_path` | no | Path to the dump to analyze. When given, it is woven into the prompt so the model starts on that file; when omitted, the model asks which dump to use. |

A dump is static, so this prompt is a one-shot: fixed command sequence, full report. The
[Analyze a crash dump](../scenarios/crash-dump.md) scenario is the manual equivalent.

## remote-triage

An investigation workflow for a live user-mode target behind a `cdb` debugging server. It
breaks into the target, orients (`r`, `k`, `~`), then follows the evidence, with `~*k` and
`!runaway` for hangs or `!analyze -v` for a crash. It closes the session at the end so the
target resumes.

| Argument | Required | Description |
| --- | --- | --- |
| `connection_string` | no | The `-remote` connection string, for example `tcp:Port=5005,Server=192.168.0.100`. |

Unlike a dump, the target is live and its state moves between commands. The prompt asks
before anything that changes execution. [Debug a remote target](../scenarios/remote-debugging.md)
is the manual equivalent.

## kernel-triage

An investigation workflow for a live kernel target over a `-k` cable. It orients
(`vertarget`, `!analyze -v`, `lm`), distinguishes a bugchecked machine from one you merely
broke into, then follows the evidence with `!process`, `!running`, and driver state.

| Argument | Required | Description |
| --- | --- | --- |
| `connection_string` | no | The `-k` connection string, for example `net:port=50000,key=1.2.3.4`. |

The whole machine is halted while the session is open, so the prompt treats releasing it
(`close_kd_session` with `resume: true`) as a required final step rather than cleanup.
[Debug a kernel target](../scenarios/kernel-debugging.md) is the manual equivalent.
