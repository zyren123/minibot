# Connect to a Streamable HTTP MCP server

Add a server entry to `config/mcp_servers.yaml` in the MiniBot app home.
Set `url` to the full MCP endpoint path.

```yaml
servers:
  - name: remote
    transport: streamable_http
    url: https://mcp.example.com/api/mcp
    headers:
      Authorization: "${MINIBOT_REMOTE_MCP_AUTHORIZATION}"
    enabled: true
```

Omit `headers` for an endpoint that accepts anonymous requests. For the example,
set `MINIBOT_REMOTE_MCP_AUTHORIZATION` in the process environment or the MiniBot
app home's `.env` file. Set the complete header value, including any `Bearer`
prefix. Configure headers in YAML. WebUI shows the endpoint URL and connection
status and can edit the URL.

Start or restart MiniBot after editing the YAML. At startup, MiniBot connects
to enabled servers and registers their tools with the agent.

## Connection behavior

MiniBot offers protocol version `2025-11-25` in its initialize request. It also
accepts a negotiated version of `2025-06-18` or `2025-03-26`. It sends the
negotiated version and any assigned session ID with subsequent requests. Each
request can receive a JSON response or a response on an SSE stream opened by
the POST. Use the existing `stdio` or `sse` entries for servers already
configured with those transports.

HTTP 404 for an assigned session marks that session as expired. Reconnect
by selecting the server in `/mcp list` or the WebUI before sending another
request. MiniBot
reports the failed operation and avoids resending it. Disconnecting attempts
to close an assigned session with HTTP DELETE.

The current client uses request-scoped SSE. Standalone GET listeners, SSE
resumption, and OAuth discovery are outside this client path. Servers that
require the stateless `2026-07-28` dialect need separate support.

[MCP 2025-11-25 transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
