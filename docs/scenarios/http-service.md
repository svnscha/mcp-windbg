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

`--host 127.0.0.1` keeps the server local. To accept connections from other machines, bind a
reachable address and open the port:

```powershell
mcp-windbg --transport streamable-http --host 0.0.0.0 --port 8000
```

!!! warning "The HTTP transport has no authentication"
    Anyone who can reach the port can drive `cdb.exe` on the host. Keep the server on `127.0.0.1`
    or a trusted network. To reach it remotely, prefer an SSH tunnel or an authenticating reverse
    proxy rather than binding `0.0.0.0` on an untrusted network. Also allow the port through the
    host firewall.

## Related

- [Command-line options](../reference/cli.md#transports) - the transport, host, and port flags.
- [Client configuration](../reference/clients.md#http-transport) - the HTTP client snippet.
