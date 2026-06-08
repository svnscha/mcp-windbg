# Prompts

The server exposes one MCP prompt. Prompts are reusable instructions your client
can insert into the conversation; most clients surface them as a slash command or
a prompt picker. Selecting one is optional, the [tools](tools.md) work without it.

## dump-triage

A comprehensive single-dump triage workflow. It walks the model through opening a
dump, extracting metadata (`vertarget`, `lm`, `k`, `.time`, `!peb`, `r`), closing
the session, and writing a structured crash report.

| Argument | Required | Description |
| --- | --- | --- |
| `dump_path` | no | Path to the dump to analyze. When given, it is woven into the prompt so the model starts on that file; when omitted, the model asks which dump to use. |

In VS Code the prompt appears as a slash command (for example `/mcp.mcp-windbg.dump-triage`);
other clients list it in their prompt menu. It drives the same tools documented in
[Tools](tools.md), so the [Analyze a crash dump](../scenarios/crash-dump.md) scenario
is the manual equivalent.
