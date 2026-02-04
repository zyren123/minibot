"""MCP tool adapter for integrating MCP tools with the tool registry."""

from typing import Any

from ..tools.base import BaseTool
from .client import MCPClient
from .protocol import MCPTool


class MCPToolAdapter(BaseTool):
    """Adapts an MCP tool to the BaseTool interface."""

    def __init__(self, mcp_tool: MCPTool, client: MCPClient):
        self._mcp_tool = mcp_tool
        self._client = client
        self.name = f"mcp__{client.name}__{mcp_tool.name}"

    @property
    def description(self) -> str:
        return f"[MCP:{self._client.name}] {self._mcp_tool.description}"

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._mcp_tool.input_schema

    async def execute(self, **kwargs) -> str:
        """Execute the MCP tool."""
        result = await self._client.call_tool(
            name=self._mcp_tool.name,
            arguments=kwargs,
        )
        return result.content
