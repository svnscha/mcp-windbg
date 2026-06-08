# End-to-end scenarios

The test suite is declarative. Each file in `../scenarios/*.yaml` describes one
end-to-end case: a sequence of tool calls against a really-hosted MCP server.
The harness spawns `python -m mcp_windbg` as a subprocess and drives it with a
real MCP `ClientSession` over stdio. The only thing faked is the LLM: the YAML
issues the tool calls a model would otherwise choose. Real server, real
transport, real `cdb.exe`.

To add a case, write a YAML file. You do not touch Python.

## Running

```powershell
uv run pytest src/mcp_windbg/tests/ -v                 # everything (needs CDB for live cases)
uv run pytest src/mcp_windbg/tests/ -v -m "not live"   # hermetic subset, no debugger needed
```

A scenario that needs a debugger is skipped (never failed) when `cdb.exe` or its
Git LFS dump is missing.

## Coverage

The server runs in a subprocess, so the parent pytest process barely touches the
server code. Set `MCP_WINDBG_COVERAGE` and the harness launches it under
`coverage run --parallel-mode`, measuring the process where tool dispatch
actually happens:

```powershell
uv run coverage erase
$env:MCP_WINDBG_COVERAGE = "1"; uv run pytest src/mcp_windbg/tests/; $env:MCP_WINDBG_COVERAGE = $null
uv run coverage combine
uv run coverage report
```

Run the full suite (with CDB) for meaningful numbers; the live scenarios are what
exercise `cdb_session.py` and the dump/remote code paths.

`streamable-http` scenarios are hard-terminated on teardown (there is no graceful
stdin EOF as with stdio), so on Windows the HTTP server process cannot flush its
coverage data. Those scenarios still verify the transport behaviorally; the
`serve_http` lines just are not counted.

## Scenario format

```yaml
name: Analyze a crash dump          # shown as the pytest case id
description: One line of intent.

transport: stdio                    # optional: stdio (default) or streamable-http

requires:                           # all optional; control clean skipping
  cdb: true                         # skip if no cdb.exe is installed
  dump: DemoCrash1.exe.7088.dmp     # skip if this dump is not present (LFS)
  remote: true                      # start a local cdb .server; bind {remote}
  # remote can also be a mapping to pick the debugged target:
  # remote: { target: ["waitfor.exe", "NoSuchSignal"] }

server:                             # optional
  args: ["--timeout", "120"]        # extra CLI flags for the hosted server

timeout: 180                        # optional per-scenario seconds (default 180)

steps:
  - call: open_windbg_dump          # a tool call
    arguments:
      dump_path: "{dump}"
      include_stack_trace: true
      include_modules: false
      include_threads: false
    expect:
      isError: false                # defaults to false for tool calls
      contains: ["Crash Analysis"]  # every substring must be present
      not_contains: ["FAILED"]
      regex: ["Exception .* occurred"]
```

### Placeholders

Resolved in every argument string (and in `server.args`):

| Placeholder | Resolves to |
| --- | --- |
| `{dump}` | absolute path of the `requires.dump` file |
| `{dumps_dir}` | the `tests/dumps` directory |
| `{scenarios_dir}` | the `tests/scenarios` directory (for fixture filter scripts) |
| `{remote}` | connection string of the harness-started remote server |
| `{cdb}` | path to the discovered `cdb.exe` (for `--cdb-path`) |

### Step kinds

| Key | Meaning |
| --- | --- |
| `call: <tool>` | Call a tool with `arguments`; assert on its text output. |
| `get_prompt: <name>` | Fetch a prompt (optional `arguments`); assert on the prompt text. |
| `list_tools: true` | List tools; assert tool names appear in `contains`. |
| `server_input: "<line>"` | Write a raw line to the remote server's stdin (e.g. `g` to resume a target). Optional `wait:` seconds after. |

### Expectations

All keys under `expect` are optional and combine with AND:

- `isError` - expected error flag for a tool call (default `false`).
- `contains` / `not_contains` - substrings that must / must not appear.
- `regex` - patterns that must match (Python `re.search`).

### Negative launch

To assert the server refuses to start (for example a malformed
`--filter-script`), set `negative_launch: true` and omit `steps`; the scenario
passes when `initialize()` fails.

## Why YAML over CDB, not mocks

We host the real server and use a real debugger so the test exercises the actual
tool dispatch, transport, session lifecycle, and `cdb.exe` interaction. The LLM
is the only component a test can meaningfully replace, so it is the only thing we
replace.
