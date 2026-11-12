# Debug from another machine

`mcp-windbg` needs Windows and `cdb.exe`, but you do not have to work on that machine. Run the
server on the Windows host that holds the dumps, symbols, and debugger, and connect to it over
HTTP from your own laptop, or let a few people share one debugging host.

## Start the server on the Windows host

```powershell
mcp-windbg --transport streamable-http --host 127.0.0.1 --port 8000
```

It serves MCP at `http://127.0.0.1:8000/mcp`. Pass the same server options as usual, for example
`--symbols-path` or `--filter-script`, see [Command-line options](../reference/cli.md).

## Point your client at it

Use an HTTP MCP server entry instead of a launched command:

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

Replace `localhost` with the host's name or IP when the client is on a different machine. From
there you debug exactly as over stdio, the [crash dump](crash-dump.md) and
[remote target](remote-debugging.md) workflows are identical.

## Paths are on the server

The server opens dumps from its own filesystem, so the paths you mention are the host's paths,
not your laptop's:

```text
Analyze the crash dump at C:\dumps\app.dmp
```

That `C:\dumps\app.dmp` is read on the Windows host.

## Expose it beyond localhost

`--host 127.0.0.1` keeps the server local. To accept connections from other machines, set a
token, bind a reachable address, name the host clients will use, and open the port in the host
firewall:

```powershell
$env:MCP_WINDBG_AUTH_TOKEN = "<a long random string>"
mcp-windbg --transport streamable-http --host 0.0.0.0 --port 8000 --allowed-host debugbox
```

Clients then send the token:

```json
{
    "servers": {
        "mcp_windbg_http": {
            "type": "http",
            "url": "http://debugbox:8000/mcp",
            "headers": { "Authorization": "Bearer <the same token>" }
        }
    }
}
```

Plain HTTP sends the token in clear text, so across an untrusted network use an SSH tunnel or a
TLS reverse proxy. See [HTTP security](../reference/cli.md#http-security) for the checks the
server applies.

## Related

- [Command-line options](../reference/cli.md#transports) - the transport, host, port, and token flags.
- [Client configuration](../reference/clients.md#http-transport) - the HTTP client snippet.
