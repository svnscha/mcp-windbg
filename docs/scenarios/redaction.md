# Redact sensitive data

Crash dumps can contain secrets, tokens, or personal data. When your MCP client sends tool output
to a cloud model, you may need to scrub that text first. A filter script does this in the server,
before anything leaves the machine.

## Write a filter script

A filter is a small Python file with a `process_input` and/or `process_output` function. Each
receives the text and a context, and returns the replacement text (or `None` to leave it
unchanged):

```python title="redact.py"
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def process_output(text, context):
    return EMAIL.sub("[redacted-email]", text)
```

- `process_output` rewrites the text returned by tools (the part the model sees).
- `process_input` rewrites string-valued tool arguments before the tool runs.

## Wire it into your client

Add `--filter-script` to the server arguments, pointing at your script:

```json
"args": ["--from", "git+https://github.com/svnscha/mcp-windbg", "mcp-windbg",
         "--filter-script", "C:\\filters\\redact.py"]
```

Now every tool result is run through `process_output` before it reaches the client.

## What the filter can and cannot see

- It sees **tool text only**: string arguments (`process_input`) and `TextContent` output
  (`process_output`). It never sees the raw MCP protocol envelope, which keeps the surface small.
- The `context` gives `hook`, `tool_name`, `transport`, and `call_id`; `process_input` also gets
  `argument_path` (such as `$.command`) and `process_output` gets `content_index`. Use `call_id`
  to correlate a call's input and output.
- It runs in-process with the server, so treat it as **trusted code**. A hook that raises is
  reported as a tool error rather than crashing the server.

## Related

- [Filter script hooks](../reference/cli.md#filter-script-hooks) - the full hook contract and a
  worked input + output example.
