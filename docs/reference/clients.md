# Client configuration

`mcp-windbg` works with any MCP client. Below are ready-to-paste snippets for the common
ones. They all use `uvx`, which fetches and runs the server on demand, so there is no
separate install step. For pip and from-source alternatives, see
[Other install methods](#other-install-methods).

All snippets set `_NT_SYMBOL_PATH` so stack traces resolve against the Microsoft symbol
server. Adjust the path or add more locations as needed.

## VS Code (GitHub Copilot)

Create `.vscode/mcp.json` in your workspace, or use **MCP: Open User Configuration** (press
++f1++) to make it available everywhere:

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

Then enable MCP: settings (++ctrl+comma++) -> search **MCP** -> enable **Model Context
Protocol** in Copilot Chat, and restart VS Code.

To pass server options such as a [filter script](../scenarios/redaction.md) or a custom CDB
path, add them to `args`:

```json
"args": ["--from", "git+https://github.com/svnscha/mcp-windbg", "mcp-windbg",
         "--cdb-path", "C:\\Program Files (x86)\\Windows Kits\\10\\Debuggers\\x64\\cdb.exe"]
```

## Claude Desktop

Add to `claude_desktop_config.json` (at `%APPDATA%\Claude\claude_desktop_config.json`):

```json title="claude_desktop_config.json"
{
    "mcpServers": {
        "mcp-windbg": {
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

Restart Claude Desktop completely after saving. For background, see
[Connect local MCP servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## Claude Code

Register the server with `claude mcp add`. The `-s user` scope makes it available in every
project; drop it to scope the server to the current project only. Everything after `--` is
the command Claude Code runs:

```bash
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- uvx --from git+https://github.com/svnscha/mcp-windbg mcp-windbg
```

If you installed the package with pip or from source, run the module directly instead of
`uvx`:

```bash
claude mcp add mcp-windbg -s user -e _NT_SYMBOL_PATH="SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols" -- python -m mcp_windbg
```

Either way Claude Code records the server in `.claude.json`:

```json title=".claude.json"
{
    "mcpServers": {
        "mcp-windbg": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "mcp_windbg"],
            "env": {
                "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
            }
        }
    }
}
```

Add server options such as a [filter script](../scenarios/redaction.md) after the command,
for example `-- python -m mcp_windbg --filter-script C:\filters\pii_redaction.py`. Run
`claude mcp list` to confirm it connected.

## Autohand Code

[Autohand Code](https://github.com/autohandai/code-cli) registers stdio servers with
`autohand mcp add <name> <command>`. Add `--scope project` to keep the registration in the
current workspace instead of the user profile:

```bash
autohand mcp add mcp-windbg uvx --from git+https://github.com/svnscha/mcp-windbg mcp-windbg
```

Autohand has no flag for a per-server `_NT_SYMBOL_PATH`; the server inherits the environment
that launched Autohand. Set `_NT_SYMBOL_PATH` in that shell (or system-wide) before starting
Autohand so stack traces resolve. If you installed the package with pip or from source,
replace the `uvx ...` command with `python -m mcp_windbg`.

## GitHub Copilot CLI

Edit `C:\Users\{username}\.copilot\mcp-config.json`:

```json title="mcp-config.json"
{
    "mcpServers": {
        "mcp-windbg": {
            "type": "local",
            "command": "uvx",
            "args": [
                "--from",
                "git+https://github.com/svnscha/mcp-windbg",
                "mcp-windbg"
            ],
            "tools": ["*"],
            "env": {
                "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
            }
        }
    }
}
```

!!! note "Use uvx, not pip, with Copilot CLI"
    Copilot CLI has had issues launching pip-installed MCP servers, see
    [copilot-cli#191](https://github.com/github/copilot-cli/issues/191). `uvx` is reliable.
    `"tools": ["*"]` enables all of the server's tools.

## HTTP transport

To run the server separately and connect over HTTP, start it with the
[streamable-http transport](cli.md#transports):

```bash
mcp-windbg --transport streamable-http --host 127.0.0.1 --port 8000
```

Then point the client at the endpoint:

```json
{
    "servers": {
        "mcp_windbg_http": {
            "type": "http",
            "url": "http://localhost:8000/mcp"
        }
    }
}
```

This transport has no authentication, so keep it on localhost or a trusted network. See
[Debug from another machine](../scenarios/http-service.md) for the full workflow.

## Other install methods

`uvx` is recommended, but you can also install the package directly.

With pip:

```bash
pip install mcp-windbg
```

```json
{
    "servers": {
        "mcp_windbg": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "mcp_windbg"],
            "env": {
                "_NT_SYMBOL_PATH": "SRV*C:\\Symbols*https://msdl.microsoft.com/download/symbols"
            }
        }
    }
}
```

From source (development):

```bash
git clone https://github.com/svnscha/mcp-windbg.git
cd mcp-windbg
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
```

```json
{
    "servers": {
        "mcp_windbg": {
            "type": "stdio",
            "command": "${workspaceFolder}/.venv/Scripts/python",
            "args": ["-m", "mcp_windbg"]
        }
    }
}
```
