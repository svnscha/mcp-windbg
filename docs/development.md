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
```

See the [end-to-end scenario guide](https://github.com/svnscha/mcp-windbg/blob/main/src/mcp_windbg/tests/e2e/README.md)
for the test format, and
[manual feature verification](https://github.com/svnscha/mcp-windbg/blob/main/src/mcp_windbg/tests/e2e/manual-verification.md)
for driving each debugging mode against a real target.
