"""Tool registry for managing available tools."""

from typing import Any

from .base import BaseTool


class ToolRegistry:
    """Registry for managing and accessing tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_all(self) -> list[BaseTool]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions for API."""
        return [tool.to_dict() for tool in self._tools.values()]

    def filter_by_names(self, names: list[str] | str) -> list[BaseTool]:
        """Filter tools by names. Use '*' for all tools."""
        if names == "*":
            return self.get_all()
        return [t for t in self._tools.values() if t.name in names]

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool by name."""
        tool = self.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        try:
            return await tool.execute(**args)
        except Exception as e:
            return f"Error: {e}"

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
