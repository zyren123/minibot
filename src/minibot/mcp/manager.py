"""MCP manager for handling multiple MCP servers."""

import asyncio
from pathlib import Path

from ..config.schema import MCPConfig, MCPServerConfig
from ..tools.registry import ToolRegistry
from .client import MCPClient
from .protocol import MCPTool, MCPResource
from .tool_adapter import MCPToolAdapter


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self, config: MCPConfig, workdir: Path):
        self.config = config
        self.workdir = workdir
        self.enabled = config.enabled

        self._clients: dict[str, MCPClient] = {}
        self._connection_locks: dict[str, asyncio.Lock] = {}

    async def connect_all(self) -> dict[str, Exception | None]:
        """Connect to all configured servers. Returns errors by server name."""
        if not self.enabled:
            return {}

        names = [server.name for server in self.config.servers if server.enabled]
        results = await asyncio.gather(*(self.connect_server(name) for name in names))
        return dict(zip(names, results))

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        names = self._clients.keys() | self._connection_locks.keys()
        await asyncio.gather(*(self.disconnect_server(name) for name in names), return_exceptions=True)

    def get_client(self, name: str) -> MCPClient | None:
        """Get a client by server name."""
        return self._clients.get(name)

    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected servers."""
        tools = []
        for client in self._clients.values():
            if client.is_connected:
                tools.extend(client.get_tools())
        return tools

    def get_all_resources(self) -> list[MCPResource]:
        """Get all resources from all connected servers."""
        resources = []
        for client in self._clients.values():
            if client.is_connected:
                resources.extend(client.get_resources())
        return resources

    def register_tools(self, registry: ToolRegistry) -> int:
        """Register all MCP tools with the tool registry. Returns count."""
        count = 0
        for client in self._clients.values():
            if not client.is_connected:
                continue
            for mcp_tool in client.get_tools():
                adapter = MCPToolAdapter(mcp_tool, client)
                registry.register(adapter)
                count += 1
        return count

    def list_servers(self) -> list[str]:
        """List all connected server names."""
        return [name for name in self._clients if self.is_connected(name)]

    def get_all_server_configs(self) -> list[MCPServerConfig]:
        """Return every configured server (connected or not)."""
        return list(self.config.servers)

    def is_connected(self, name: str) -> bool:
        """Whether a server is currently connected."""
        client = self._clients.get(name)
        return client is not None and client.is_connected

    async def connect_server(self, name: str) -> Exception | None:
        """Connect a single server by config name. Returns error or None."""
        cfg = next((s for s in self.config.servers if s.name == name), None)
        if cfg is None:
            return ValueError(f"Unknown server: {name}")
        async with self._connection_locks.setdefault(name, asyncio.Lock()):
            client = self._clients.get(name)
            if client is None:
                client = MCPClient(cfg, self.workdir)
            try:
                await client.connect()
            except Exception as exc:
                return exc
            self._clients[name] = client
        return None

    async def disconnect_server(self, name: str) -> None:
        """Disconnect a single server by name."""
        async with self._connection_locks.setdefault(name, asyncio.Lock()):
            client = self._clients.pop(name, None)
            if client is not None:
                await client.disconnect()

    @property
    def server_count(self) -> int:
        """Number of connected servers."""
        return len(self.list_servers())

    @property
    def tool_count(self) -> int:
        """Total number of MCP tools."""
        return len(self.get_all_tools())
