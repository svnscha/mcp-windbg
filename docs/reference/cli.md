# Command-line options

The server is started by your MCP client, but the same options apply when you run it by hand.
With `uvx` it is fetched and run on demand:

```bash
uvx --from git+https://github.com/svnscha/mcp-windbg mcp-windbg --help
```

Installed with pip, the entry point is `mcp-windbg` (or `python -m mcp_windbg`).

## All options

| Option | Default | Description |
| --- | --- | --- |
| `--transport {stdio,streamable-http}` | `stdio` | Transport protocol. See [Transports](#transports). |
| `--host HOST` | `127.0.0.1` | Host to bind for the HTTP transport. |
| `--port PORT` | `8000` | Port to bind for the HTTP transport. |
| `--cdb-path PATH` | auto-detect | Full path to `cdb.exe`. See [Symbols and CDB](#symbols-and-cdb). |
| `--symbols-path PATH` | `_NT_SYMBOL_PATH` | Symbol search path used when opening a session. |
| `--no-dump-dir-symbols` | off | Do not auto-add a dump's own directory to the symbol path. |
| `--filter-script PATH` | none | Python script with tool-text hooks. See [Filter script hooks](#filter-script-hooks). |
| `--timeout SECONDS` | `30` | Per-command timeout. |
| `--verbose` | off | Verbose logging to stderr. |

## General

- **`--timeout`** bounds how long any single debugger command may run. Raise it for heavy
  analysis on large dumps, for example `--timeout 120`.
- **`--verbose`** logs to stderr, which is safe under stdio (stdout is the MCP transport).

## Transports

| Transport | Use it for |
| --- | --- |
| `stdio` (default) | Local MCP clients that launch the server as a child process: VS Code, Claude Desktop, Copilot CLI. |
| `streamable-http` | Running the server separately and connecting over HTTP. |

Standard I/O (default):

```bash
mcp-windbg
# equivalently
mcp-windbg --transport stdio
```

Streamable HTTP:

```bash
mcp-windbg --transport streamable-http --host 127.0.0.1 --port 8000
```

The endpoint is then `http://127.0.0.1:8000/mcp`. See
[Client configuration](clients.md#http-transport) for the matching client snippet and
[Debug from another machine](../scenarios/http-service.md) for the full workflow.

!!! warning "The HTTP transport has no authentication"
    Anyone who can reach the port can drive `cdb.exe` on the host. Keep `--host 127.0.0.1`, or
    expose it only on a trusted network or behind an SSH tunnel or authenticating proxy.

## Symbols and CDB

- **`--cdb-path`** - the server auto-detects `cdb.exe` in the common Windows Kits and
  Microsoft Store locations. Set this when yours is installed elsewhere.
- **`--symbols-path`** - sets the symbol search path for new sessions. If omitted, the
  debugger uses `_NT_SYMBOL_PATH` from the environment, which is the usual way to configure
  the Microsoft symbol server (see [Getting started](../getting-started.md)).
- **Dump directory symbols** - when a session is created for a dump, the server prepends the
  dump's own directory to the symbol path so co-located `.pdb` files are found. Disable this
  with **`--no-dump-dir-symbols`**.

Per-call symbol paths are also available on some tools, see
[`open_windbg_dump`](tools.md#open_windbg_dump) and
[`run_windbg_cmd`](tools.md#run_windbg_cmd).

## Filter script hooks

Use `--filter-script` to load a small Python helper that rewrites **tool text only**, for
example to [redact PII](../scenarios/redaction.md) before it leaves the machine. The script
never sees the full MCP JSON-RPC envelope, which keeps the hook surface small and avoids
protocol interference. It
runs in-process with the server, so treat it as trusted code.

The script may define either or both of these functions:

```python
def process_input(text, context):
    return text


def process_output(text, context):
    return text
```

- `process_input` is applied to string-valued tool arguments before the tool runs.
- `process_output` is applied to `TextContent.text` values returned by tools before they go
  back to the client.
- `text` is always a plain `str`.
- `context` includes `hook`, `tool_name`, `transport`, and `call_id`.
- Input callbacks also receive `argument_path` such as `$.command` or `$.payload.notes[0]`.
- Output callbacks also receive `content_index` for the returned text item.
- `call_id` is stable for the lifetime of one tool invocation, so input and output for the
  same call can be correlated.
- A hook may return `None` to leave the text unchanged, or a replacement string.

Example redaction filter:

```python
def process_input(text, context):
    if context["tool_name"] == "run_windbg_cmd" and context["argument_path"] == "$.command":
        return text.replace("user@example.com", "[redacted-email]")
    return text


def process_output(text, context):
    return text.replace("user@example.com", "[redacted-email]")
```

Start the server with the filter enabled:

```bash
mcp-windbg --filter-script C:\filters\pii_redaction.py
```
