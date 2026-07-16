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

A scenario that needs a debugger is skipped (never failed) when `cdb.exe` is
missing. The Git LFS dumps are mandatory: a scenario whose `requires.dump` file
is absent hard-fails (run `git lfs pull`), so a half-set-up checkout is a loud
error rather than silent green.

Kernel scenarios (`requires.kernel`) drive a real kernel target, which CI does not
have, so they skip unless one is configured:

```powershell
$env:MCP_WINDBG_KERNEL_CONNECTION = "net:port=50005,key=1.2.3.4"   # the -k connection string
uv run pytest src/mcp_windbg/tests/ -m kernel -v
```

## Coverage

The code runs in two processes and both must be measured. The scenarios drive a
server subprocess: set `MCP_WINDBG_COVERAGE` and the harness launches it under
`coverage run --parallel-mode`, measuring where tool dispatch actually happens.
The hermetic unit tests (`tests/test_*.py`) run in the pytest process itself, so
pytest must be started under coverage too - plain `pytest` reports those tests'
code as never executed:

```powershell
uv run coverage erase
$env:MCP_WINDBG_COVERAGE = "1"
uv run coverage run -m pytest src/mcp_windbg/tests/     # measures pytest AND the server
$env:MCP_WINDBG_COVERAGE = $null
uv run coverage combine
uv run coverage report
```

Run the full suite (with CDB) for meaningful numbers; the live scenarios are what
exercise `cdb_session.py` and the dump/remote code paths. Add a kernel target
(`MCP_WINDBG_KERNEL_CONNECTION`, above) to cover `kd_session.py` for real rather
than through its fake-`kd.exe` unit tests.

The suite holds at ~91%. CI enforces a floor with `coverage report --fail-under=88`;
the margin absorbs small cross-version and cdb-output differences across the
Windows matrix. Code that genuinely cannot be line-measured end-to-end is excluded in
`pyproject.toml` (`exclude_also`) or with a commented `# pragma: no cover`:

- the `streamable-http` transport, which is hard-terminated on Windows teardown
  (no graceful stdin EOF), so the process cannot flush its coverage. The HTTP
  scenario still verifies the transport behaviorally; the `serve_http` lines just
  are not counted;
- the atexit session cleanup (runs after coverage stops);
- debug-only output and low-level IO error handlers, and defensive guards the
  MCP layer already enforces before they could run.

## Scenario format

```yaml
name: Analyze a crash dump          # shown as the pytest case id
description: One line of intent.

transport: stdio                    # optional: stdio (default) or streamable-http

requires:                           # all optional
  cdb: true                         # skip if no cdb.exe is installed
  dump: DemoCrash1.exe.7088.dmp     # mandatory LFS dump; missing it hard-fails
  remote: true                      # start a local cdb .server; bind {remote}
  # remote can also be a mapping to pick the debugged target:
  # remote: { target: ["waitfor.exe", "NoSuchSignal"] }
  kernel: true                      # needs kd.exe + a real kernel target; bind {kernel}

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
| `{kd}` | path to the discovered `kd.exe` (for `--kd-path`) |
| `{kernel}` | the `-k` connection string from `MCP_WINDBG_KERNEL_CONNECTION` |

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
