import asyncio
import builtins
import json
from pathlib import Path

import httpx
import pytest

from src.minibot.config.schema import MCPConfig, MCPServerConfig
from src.minibot.config.settings import load_config
from src.minibot.mcp.client import MCPClient
from src.minibot.mcp.manager import MCPManager
from src.minibot.mcp.streamable_http import StreamableHTTPTransport
from src.minibot.tools.registry import ToolRegistry


ENDPOINT = "https://mcp.example.invalid/integrations/mcp?tenant=test"
SESSION_ID = "session-from-server"
AUTH = "Bearer synthetic-token"


@pytest.fixture
def install_peer(monkeypatch):
    original_client = httpx.AsyncClient

    def install(handler):
        opened = []

        def make_client(**kwargs):
            client = original_client(transport=httpx.MockTransport(handler), **kwargs)
            opened.append(client)
            return client

        monkeypatch.setattr("src.minibot.mcp.streamable_http.httpx.AsyncClient", make_client)
        return opened

    return install


def reply(request, result, *, headers=None):
    message = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}, headers=headers)


def init_result(version="2025-11-25", *, tools=True, resources=False):
    return {
        "serverInfo": {"name": "mock", "version": "1"},
        "protocolVersion": version,
        "capabilities": {name: {} for name, present in (("tools", tools), ("resources", resources)) if present},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18", "2025-03-26"])
async def test_configured_server_discovers_and_calls_tools_via_json_and_sse(tmp_path, monkeypatch, install_peer, version):
    app_home = tmp_path / "app"
    config_file = app_home / "config" / "mcp_servers.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(f"""servers:
  - name: distant
    transport: streamable_http
    url: '{ENDPOINT}'
    headers:
      Authorization: '${{MINIBOT_TEST_MCP_AUTH}}'
""", encoding="utf-8")
    monkeypatch.setenv("MINIBOT_TEST_MCP_AUTH", AUTH)
    seen_encodings = []
    original_open = builtins.open

    def watched_open(file, mode="r", *args, **kwargs):
        if file == config_file and "r" in mode:
            seen_encodings.append(kwargs.get("encoding"))
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watched_open)
    cfg = load_config(workdir=tmp_path, project_root=tmp_path, app_home=app_home)
    assert seen_encodings == ["utf-8"]
    assert cfg.mcp.servers[0].headers == {"Authorization": AUTH}
    assert AUTH not in repr(cfg.mcp.servers[0])

    methods = []
    ids = []
    session_id = None if version == "2025-03-26" else SESSION_ID

    async def handler(request):
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == AUTH
        assert request.headers["accept"] == "application/json, text/event-stream"
        if request.method == "DELETE":
            assert request.headers["mcp-session-id"] == SESSION_ID
            assert request.headers["mcp-protocol-version"] == version
            methods.append("DELETE")
            return httpx.Response(405)

        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        message = json.loads(request.content)
        method = message.get("method", "reply")
        methods.append(method)
        assert message["jsonrpc"] == "2.0"
        if "id" in message and method != "reply":
            ids.append(message["id"])
        if method == "initialize":
            assert "mcp-session-id" not in request.headers
            assert message["params"]["protocolVersion"] == "2025-11-25"
            headers = {"MCP-Session-Id": session_id} if session_id else {}
            result = init_result(version, resources=True)
            if version == "2025-06-18":
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
                headers["Content-Type"] = "text/event-stream"
                return httpx.Response(200, headers=headers, content=f"data: {json.dumps(response)}\n\n")
            return reply(request, result, headers=headers)

        assert request.headers.get("mcp-session-id") == session_id
        assert request.headers["mcp-protocol-version"] == version
        if method == "notifications/initialized":
            assert "id" not in message
            return httpx.Response(202)
        if method == "tools/list":
            return reply(request, {"tools": [{"name": "echo", "description": "工具", "inputSchema": {"type": "object"}}]})
        if method == "resources/list":
            return reply(request, {"resources": [{"uri": "memory://note", "name": "note"}]})
        if method == "resources/read":
            assert message["params"] == {"uri": "memory://note"}
            return reply(request, {"contents": [{"text": "资源"}]})
        if method == "tools/call":
            assert message["params"] == {"name": "echo", "arguments": {"text": "你好"}}
            return httpx.Response(200, headers={"Content-Type": "text/event-stream; charset=UTF-8"}, content=(
                "\ufeff: heartbeat\r\n\r\n"
                "id: prime\r\ndata:\r\n\r\n"
                'data: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\n'
                'data: {"jsonrpc":"2.0","id":"unrelated","result":{}}\r\n\r\n'
                'data: {"jsonrpc":"2.0","id":"server-ping","method":"ping"}\r\n\r\n'
                f'data: {{"jsonrpc":"2.0","id":{message["id"]},\r\n'
                'data: "result":{"content":[{"type":"text","text":"你好"}]}}\r\n\r\n'
            ).encode("utf-8"))
        assert message == {"jsonrpc": "2.0", "id": "server-ping", "result": {}}
        return httpx.Response(202)

    opened = install_peer(handler)
    manager = MCPManager(cfg.mcp, tmp_path)
    try:
        assert await manager.connect_all() == {"distant": None}
        assert manager.is_connected("distant")
        registry = ToolRegistry()
        assert manager.register_tools(registry) == 1
        output = await registry.get("mcp__distant__echo").execute_result(text="你好")
        assert (output.status, output.text) == ("success", "你好")
        assert await manager.get_client("distant").read_resource("memory://note") == "资源"
    finally:
        await manager.disconnect_all()

    assert len(ids) == len(set(ids)) == 5
    assert methods == [
        "initialize", "notifications/initialized", "tools/list", "resources/list",
        "tools/call", "reply", "resources/read",
    ] + (["DELETE"] if session_id else [])
    assert opened[0].is_closed
    assert not manager.is_connected("distant")


@pytest.mark.asyncio
@pytest.mark.parametrize(("content_type", "body", "error"), [
    ("application/json", b'{"jsonrpc":"2.0","id":true,"result":{}}', "response ID"),
    ("application/json", b'{"jsonrpc":"2.0","id":1,"result":{},"error":{}}', "Invalid MCP JSON-RPC response"),
    ("application/json", b'{"jsonrpc":"2.0","id":1,"error":{"message":"Server refused test"}}', "Server refused test"),
    ("application/json", b"not JSON", "Malformed MCP JSON-RPC"),
    ("text/event-stream", b'data: {"jsonrpc":"2.0","id":7,"result":{}}\n\n', "ended before the matching"),
    ("text/html", b"<html>", "Expected an MCP JSON or text/event-stream"),
])
async def test_rejects_invalid_or_unmatched_responses(install_peer, content_type, body, error):
    install_peer(lambda _: httpx.Response(200, headers={"Content-Type": content_type}, content=body))
    transport = StreamableHTTPTransport(ENDPOINT)
    await transport.connect()
    try:
        with pytest.raises(RuntimeError, match=error):
            await transport.send({"method": "tools/list"})
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_expired_session_requires_explicit_reconnect_without_tool_replay(tmp_path, install_peer):
    initializations = 0
    sessions_called = []

    async def handler(request):
        nonlocal initializations
        if request.method == "DELETE":
            assert request.headers["mcp-session-id"] == "2"
            return httpx.Response(204)
        message = json.loads(request.content)
        method = message["method"]
        if method == "initialize":
            assert "mcp-session-id" not in request.headers
            initializations += 1
            return reply(request, init_result(), headers={"MCP-Session-Id": str(initializations)})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return reply(request, {"tools": [{"name": "echo"}]})
        assert method == "tools/call"
        session = request.headers["mcp-session-id"]
        sessions_called.append(session)
        if session == "1":
            return httpx.Response(404)
        return reply(request, {"content": [{"type": "text", "text": "new session"}]})

    opened = install_peer(handler)
    manager = MCPManager(MCPConfig(servers=[MCPServerConfig("remote", "streamable_http", url=ENDPOINT)]), tmp_path)
    await manager.connect_server("remote")
    registry = ToolRegistry()
    manager.register_tools(registry)
    adapter = registry.get("mcp__remote__echo")
    outcome = await adapter.execute_result()
    assert outcome.is_error and "MCP session expired (HTTP 404)" in outcome.text
    assert sessions_called == ["1"]
    assert manager.list_servers() == []
    assert manager.get_all_tools() == []
    assert not manager.is_connected("remote")

    assert await asyncio.gather(
        manager.connect_server("remote"), manager.connect_server("remote"),
    ) == [None, None]
    assert manager.is_connected("remote")
    assert opened[0].is_closed
    assert (await adapter.execute_result()).text == "new session"
    assert sessions_called == ["1", "2"]
    assert initializations == 2
    await manager.disconnect_all()
    assert opened[1].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_cancellation_closes_sse_body_and_notifies_server(install_peer, mode):
    started = asyncio.Event()
    closed = asyncio.Event()
    notifications = []

    class Heartbeats(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            while True:
                yield b": keepalive\n\n"
                await asyncio.sleep(0.005)

        async def aclose(self):
            closed.set()

    async def handler(request):
        message = json.loads(request.content)
        if message["method"] == "tools/list":
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Heartbeats())
        notifications.append(message)
        return httpx.Response(202)

    install_peer(handler)
    transport = StreamableHTTPTransport(ENDPOINT, timeout=0.04 if mode == "timeout" else 5)
    await transport.connect()
    try:
        task = asyncio.create_task(transport.send({"method": "tools/list"}))
        await asyncio.wait_for(started.wait(), 1)
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 1)
        assert closed.is_set()
        assert notifications == [
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        ]
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unsupported_version", "invalid_ack"])
async def test_failed_initialization_releases_session_and_closes_client(install_peer, failure):
    seen = []

    async def handler(request):
        if request.method == "DELETE":
            assert request.headers["mcp-session-id"] == SESSION_ID
            seen.append("DELETE")
            return httpx.Response(204)
        method = json.loads(request.content)["method"]
        seen.append(method)
        if method == "initialize":
            version = "2026-07-28" if failure == "unsupported_version" else "2025-11-25"
            return reply(request, init_result(version), headers={"MCP-Session-Id": SESSION_ID})
        assert method == "notifications/initialized"
        return httpx.Response(204)

    opened = install_peer(handler)
    client = MCPClient(MCPServerConfig("remote", "streamable_http", url=ENDPOINT))
    with pytest.raises(RuntimeError, match="protocol version|did not accept"):
        await client.connect()
    assert not client.is_connected
    assert opened[0].is_closed
    assert seen == (
        ["initialize", "DELETE"] if failure == "unsupported_version"
        else ["initialize", "notifications/initialized", "DELETE"]
    )
