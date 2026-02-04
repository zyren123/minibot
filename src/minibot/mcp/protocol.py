"""MCP protocol data types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPTool:
    """MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server: str = ""


@dataclass
class MCPToolCall:
    """MCP tool call request."""

    name: str
    arguments: dict[str, Any]


@dataclass
class MCPToolResult:
    """MCP tool call result."""

    content: str
    is_error: bool = False


@dataclass
class MCPResource:
    """MCP resource definition."""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    server: str = ""


@dataclass
class MCPCapabilities:
    """Server capabilities."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False


@dataclass
class MCPServerInfo:
    """MCP server information."""

    name: str
    version: str
    capabilities: MCPCapabilities = field(default_factory=MCPCapabilities)
