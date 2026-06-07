# Troubleshooting

Common problems and fixes. If none of these help, enable
[verbose logging](#diagnostics) and open a
[GitHub issue](https://github.com/svnscha/mcp-windbg/issues) with the details.

## Tools do not appear in the client

- Confirm MCP is enabled in the client (in VS Code: settings -> **MCP** -> enable in Copilot
  Chat) and restart it.
- Check that the config file is valid JSON and in the right place, see
  [Client configuration](reference/clients.md).
- In VS Code, check **Output -> Model Context Protocol** for startup errors.
- Verify the server runs at all:

    ```bash
    uvx --from git+https://github.com/svnscha/mcp-windbg mcp-windbg --help
    ```

## CDB not found

The server auto-detects `cdb.exe`, but cannot if it is installed somewhere unusual.

- Confirm it is reachable: `cdb.exe -version`.
- Install Debugging Tools for Windows (WinDbg from the Microsoft Store, or the Windows SDK).
- Point the server at it explicitly with [`--cdb-path`](reference/cli.md#symbols-and-cdb):

    ```json
    "args": ["--from", "git+https://github.com/svnscha/mcp-windbg", "mcp-windbg",
             "--cdb-path", "C:\\Program Files (x86)\\Windows Kits\\10\\Debuggers\\x64\\cdb.exe"]
    ```

## Symbols do not load

Stack traces full of offsets and no function names mean symbols are not resolving.

- Set `_NT_SYMBOL_PATH` in the client `env`, see [Getting started](getting-started.md):

    ```json
    "env": {"_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"}
    ```

- For your own binaries, keep the matching `.pdb` next to the dump (the dump's directory is
  added automatically) or pass an extra path, see [Symbols and CDB](reference/cli.md#symbols-and-cdb).
- Confirm the machine can reach `https://msdl.microsoft.com`.

## Command timeouts

A command that exceeds the per-command limit reports a timeout.

- Raise it with [`--timeout`](reference/cli.md#general), for example `--timeout 120`.
- On large dumps, prefer targeted commands over broad analysis.

## Module not found (pip installs)

`No module named 'mcp_windbg'` from a pip-based config:

- Verify the install: `pip list | findstr mcp-windbg`.
- For a virtual environment, point `command` at that interpreter, for example
  `${workspaceFolder}/.venv/Scripts/python`. Or use `uvx`, which avoids this entirely.

## Remote debugging issues

Cannot connect to a remote target:

- Use a supported [connection string format](reference/tools.md#open_windbg_remote), for
  example `tcp:Port=5005,Server=192.168.0.100`.
- Check network reachability to the target and that the debugging server is listening.
- Check the target's firewall.
- Note that kernel-mode (`-k`) debugging is not supported, see
  [Debug a remote target](scenarios/remote-debugging.md).

## Server crashes when run directly in a terminal

`mcp-windbg` speaks MCP over stdin/stdout; it is meant to be launched by a client, not typed
into interactively. Run it through your MCP client, or with `--help` to see options.

## Diagnostics

Enable verbose logging (goes to stderr, safe under stdio):

```json
"args": ["--from", "git+https://github.com/svnscha/mcp-windbg", "mcp-windbg", "--verbose"]
```

Check the basics:

```bash
cdb.exe -version
uvx --from git+https://github.com/svnscha/mcp-windbg mcp-windbg --help
```
