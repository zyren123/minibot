"""MCP manager for handling multiple MCP servers."""

import asyncio
from pathlib import Path

from ..config.schema import MCPConfig
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

    async def connect_all(self) -> dict[str, Exception | None]:
        """Connect to all configured servers. Returns errors by server name."""
        if not self.enabled:
            return {}

        errors: dict[str, Exception | None] = {}

        # Connect to each server in parallel
        tasks = []
        for server_config in self.config.servers:
            if server_config.enabled:
                client = MCPClient(server_config, self.workdir)
                self._clients[server_config.name] = client
                tasks.append((server_config.name, client.connect()))

        results = await asyncio.gather(
            *[t[1] for t in tasks],
            return_exceptions=True,
        )

        for (name, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                errors[name] = result
                # Remove failed client
                self._clients.pop(name, None)
            else:
                errors[name] = None

        return errors

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        tasks = [client.disconnect() for client in self._clients.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()

    def get_client(self, name: str) -> MCPClient | None:
        """Get a client by server name."""
        return self._clients.get(name)

    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected servers."""
        tools = []
        for client in self._clients.values():
            tools.extend(client.get_tools())
        return tools

    def get_all_resources(self) -> list[MCPResource]:
        """Get all resources from all connected servers."""
        resources = []
        for client in self._clients.values():
            resources.extend(client.get_resources())
        return resources

    def register_tools(self, registry: ToolRegistry) -> int:
        """Register all MCP tools with the tool registry. Returns count."""
        count = 0
        for client in self._clients.values():
            for mcp_tool in client.get_tools():
                adapter = MCPToolAdapter(mcp_tool, client)
                registry.register(adapter)
                count += 1
        return count

    def list_servers(self) -> list[str]:
        """List all connected server names."""
        return list(self._clients.keys())

    @property
    def server_count(self) -> int:
        """Number of connected servers."""
        return len(self._clients)

    @property
    def tool_count(self) -> int:
        """Total number of MCP tools."""
        return sum(len(c.get_tools()) for c in self._clients.values())
