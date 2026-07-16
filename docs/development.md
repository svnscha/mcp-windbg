# Development

Run `mcp-windbg` from a local checkout so an MCP client uses your working tree, with source
edits picked up live. This is the setup for working on the server itself.

## Editable install

An editable install links the package to your checkout instead of copying it, so changes to
the source (and switching branches) take effect the next time the server starts. Install into
your user site with pip:

```powershell
git clone https://github.com/svnscha/mcp-windbg.git
cd mcp-windbg
python -m pip install --user -e .
```

This puts `mcp-windbg.exe` in your user scripts directory (for example
`%APPDATA%\Python\Python314\Scripts`). That directory is often not on `PATH`, which is fine:
the client configs below launch the module with a full Python path instead, so `PATH` does
not matter.

Confirm the install points at your checkout:

```powershell
python -c "import mcp_windbg, inspect; print(inspect.getfile(mcp_windbg))"
```

It should print the path inside your clone (`...\mcp-windbg\src\mcp_windbg\__init__.py`).

!!! tip "uv alternative"
    `uv sync --dev` already installs the project editable into `.venv`, so `uv run mcp-windbg`
    runs your checkout without a separate install. Use that for running the tests; use the
    `--user` install above when you want an MCP client to launch the dev build directly.

## Point an MCP client at the dev build

Use a distinct server name (for example `mcp-windbg-dev`) so it does not clash with a released
install, and launch the module with your full Python path so the editable install resolves
regardless of `PATH`. Add `--verbose` to see the debugger commands the server runs.

=== "Claude Code"

    ```bash
    claude mcp add mcp-windbg-dev -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- C:\Python314\python.exe -m mcp_windbg --verbose
    ```

=== "Claude Desktop"

    Edit `%APPDATA%\Claude\claude_desktop_config.json`:

    ```json title="claude_desktop_config.json"
    {
        "mcpServers": {
            "mcp-windbg-dev": {
                "command": "C:\\Python314\\python.exe",
                "args": ["-m", "mcp_windbg", "--verbose"],
                "env": {
                    "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
                }
            }
        }
    }
    ```

=== "VS Code (GitHub Copilot)"

    Edit `.vscode/mcp.json`:

    ```json title=".vscode/mcp.json"
    {
        "servers": {
            "mcp_windbg_dev": {
                "type": "stdio",
                "command": "C:\\Python314\\python.exe",
                "args": ["-m", "mcp_windbg", "--verbose"],
                "env": {
                    "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
                }
            }
        }
    }
    ```

Replace `C:\Python314\python.exe` with the interpreter you ran `pip install --user` against
(`python -c "import sys; print(sys.executable)"`). Restart the MCP client after editing source
or switching branches so it relaunches the server on the new code.

## Build a wheel

To produce the distributable artifacts (a wheel and an sdist under `dist/`), for example to
test packaging before a release:

```powershell
uv build
```

This is a built artifact, not an editable install: reinstall it to pick up later source
changes. For day-to-day development, prefer the editable install above.

## Tests

```powershell
uv run pytest src/mcp_windbg/tests/ -v                 # full suite (needs cdb.exe for live cases)
uv run pytest src/mcp_windbg/tests/ -v -m "not live"   # hermetic subset, no debugger needed
uv run pytest src/mcp_windbg/tests/ -v -m kernel       # kernel scenarios, needs a real target
```

Markers gate what needs hardware: `live` needs `cdb.exe`, `remote` starts a local CDB server,
and `kernel` needs `kd.exe` plus a target machine. Whatever is unavailable skips rather than
fails, so the hermetic subset stays green anywhere.

The `scenarios/*.yaml` files are not a separate runner: each becomes one parametrized case,
addressed by its `name:` field rather than its filename.

```powershell
uv run pytest "src/mcp_windbg/tests/test_scenarios.py::test_scenario[Kernel session against a real target]" -v
```

### Kernel target

Kernel scenarios attach to a real target, so they need its `-k` connection string in
`MCP_WINDBG_KERNEL_CONNECTION`. Unset, they skip (as they do on CI, which has no target):

```powershell
$env:MCP_WINDBG_KERNEL_CONNECTION = "net:port=50005,key=1.2.3.4"
uv run pytest src/mcp_windbg/tests/ -m kernel -v
```

The hermetic tests in `tests/test_kd_session.py` drive a fake `kd.exe` instead. They are the
floor CI can reach, not proof the feature works: run the `kernel` marker against a real target
before shipping a kernel change.

### VS Code

`.vscode/settings.json` is set up for discovery already. Two things there are load-bearing:

- **`python.testing.pytestArgs` stays empty.** Pytest then falls back to `testpaths` in
  `pyproject.toml` (`src/mcp_windbg/tests`). A path argument overrides `testpaths`, so
  discovery and the command line silently disagree about what the suite is.
- **`python.envFile` and `python.terminal.useEnvFile`** point at `.env` and apply it, since VS
  Code does not inherit your shell. Both are needed: `envFile` alone does not reach the test
  run, which goes through a terminal. Create the file to run the kernel scenarios from the Test
  Explorer. It is gitignored, as it holds your KDNET key:

```ini title=".env"
MCP_WINDBG_KERNEL_CONNECTION=net:port=50005,key=1.2.3.4
```

### Coverage

The code runs in two processes and both must be measured, or the number lies. The scenarios
drive a server subprocess (`MCP_WINDBG_COVERAGE` launches it under `coverage run`), while the
hermetic tests run in the pytest process itself, so pytest needs starting under coverage too:

```powershell
uv run coverage erase
$env:MCP_WINDBG_COVERAGE = "1"
uv run coverage run -m pytest src/mcp_windbg/tests/     # measures pytest AND the server
$env:MCP_WINDBG_COVERAGE = $null
uv run coverage combine                 # merge the per-process .coverage.* files
uv run coverage report                  # or: uv run coverage html  ->  htmlcov/
```

Plain `pytest` reports every hermetic test's code as never executed, which is why the Test
Explorer's coverage numbers are not trustworthy. CI gates on `--fail-under=88`; leave
`MCP_WINDBG_KERNEL_CONNECTION` unset to reproduce its number, set it for the honest local one.

#### What the scenarios alone cover

To ask what the end-to-end suite reaches on its own, run only the scenarios and drop the
`coverage run -m` wrapper: they exercise the server subprocess, and the pytest process is just
the client driving it, so measuring the parent adds nothing.

```powershell
uv run coverage erase
$env:MCP_WINDBG_COVERAGE = "1"
uv run pytest src/mcp_windbg/tests/test_scenarios.py
$env:MCP_WINDBG_COVERAGE = $null
uv run coverage combine
uv run coverage html -d htmlcov-e2e      # browsable, line by line -> htmlcov-e2e/index.html
```

Any marker or path narrows it, and `--include` trims the report to the modules you care about.
This answers "what does this one scenario actually touch?" in seconds:

```powershell
uv run pytest src/mcp_windbg/tests/ -m kernel
uv run coverage combine
uv run coverage report --include="*kd_session*,*debug_session*"
```

!!! warning "Always start with `coverage erase`"
    `combine` folds new data into an existing `.coverage`, so skipping the erase silently
    unions this run with the last one, exactly when you are trying to isolate a subset. Note
    that `erase` also deletes everything matching `.coverage.*`, including a snapshot you named
    with that prefix. To keep data files around, point `COVERAGE_FILE` somewhere outside that
    glob.

See the [end-to-end scenario guide](https://github.com/svnscha/mcp-windbg/blob/main/src/mcp_windbg/tests/e2e/README.md)
for the test format.
