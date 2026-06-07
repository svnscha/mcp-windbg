# Getting started

This page takes you from nothing to your **first crash dump analysis**. It uses VS Code
with GitHub Copilot and `uvx`, which is the quickest path. Other clients work the same way
once the server is configured, see [Client configuration](reference/clients.md).

## 1. Check the prerequisites

You need a 64-bit Windows machine with:

- **Debugging Tools for Windows**, which ships `cdb.exe`. The simplest install is WinDbg
  from the Microsoft Store:

    ```powershell
    winget install 9PGJGD53TN86 --accept-source-agreements --accept-package-agreements
    ```

    Alternatively install the **Windows SDK** or **WDK** and tick *Debugging Tools for
    Windows*. The server auto-detects `cdb.exe` in the usual locations; if yours is
    elsewhere, you will pass `--cdb-path` later.

- **`uv`**, a fast Python package manager that also provides `uvx`:

    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

    Prefer plain Python? Install Python 3.10+ instead and use `pip`, see
    [Client configuration](reference/clients.md).

- **VS Code** with the **GitHub Copilot** extension, and MCP enabled (step 3).

!!! tip "Quick sanity check"
    Confirm `cdb.exe` is reachable before you start:

    ```powershell
    cdb.exe -version
    ```

    If that fails, note the full path to `cdb.exe` and pass it with `--cdb-path`.

## 2. Configure the server in VS Code

Create `.vscode/mcp.json` in your workspace. With `uvx`, there is nothing to install first,
it fetches and runs the server on demand:

```json title=".vscode/mcp.json"
{
    "servers": {
        "mcp_windbg": {
            "type": "stdio",
            "command": "uvx",
            "args": [
                "--from",
                "git+https://github.com/svnscha/mcp-windbg",
                "mcp-windbg"
            ],
            "env": {
                "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
            }
        }
    }
}
```

The `_NT_SYMBOL_PATH` line points the debugger at the Microsoft symbol server (cached under
`C:\Symbols`), which is what makes stack traces readable. Adjust as needed, see
[Command-line options](reference/cli.md) for symbol and CDB path handling.

!!! tip "Escape backslashes in JSON"
    JSON treats `\` as an escape character. Write Windows paths with doubled backslashes
    (`C:\\Symbols`) or forward slashes.

## 3. Enable MCP in Copilot

1. Open VS Code settings (++ctrl+comma++).
2. Search for **MCP**.
3. Enable **Model Context Protocol** in Copilot Chat.
4. Restart VS Code so it picks up `.vscode/mcp.json`.

## 4. Analyze your first dump

Open Copilot Chat (agent mode) and ask, in plain language:

```text
Analyze the crash dump at C:\dumps\app.dmp
```

Copilot calls the `open_windbg_dump` tool, which runs the common triage commands and returns
the crash information, the `!analyze -v` result, the stack trace, modules, and threads. From
there you keep asking:

```text
Show the call stack with kb and explain the access violation
Run .ecxr then u to disassemble around the fault
Check the heap around 0x1f2a0040 with !heap -p -a 0x1f2a0040
```

Each request becomes a `run_windbg_cmd` call against the same open session. When you are
done, ask it to close the session to free the `cdb.exe` process:

```text
Close the crash dump session for C:\dumps\app.dmp
```

## Where to go next

- **[Analyze a crash dump](scenarios/crash-dump.md)** - the dump workflow in depth.
- **[Debug a remote target](scenarios/remote-debugging.md)** - connect to a live session and break in.
- **[Triage multiple dumps](scenarios/triage.md)** - scan a folder and compare.
- **[Command-line options](reference/cli.md)** - every CLI flag and transport.
- **[Tools](reference/tools.md)** - the MCP tools and their parameters.
- **[Client configuration](reference/clients.md)** - Claude Desktop, Copilot CLI, pip, and source installs.
- **[Troubleshooting](troubleshooting.md)** - when something does not work.
