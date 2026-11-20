"""Tool registry for managing available tools."""

from typing import Any

from ..core.runtime_budget import RuntimeBudgetExceeded
from .base import BaseTool
from .result import ToolOutcome


class ToolRegistry:
    """Registry for managing and accessing tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._sealed = False

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        if self._sealed:
            raise ValueError("Host tools are unavailable in a restricted Docker workspace")
        self._tools[tool.name] = tool

    def seal(self) -> None:
        """Prevent additions once the permitted tools have been registered."""
        self._sealed = True

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
        """Execute a tool through the compatible text API."""
        return (await self.execute_result(name, args)).text

    async def execute_result(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        """Execute a tool without discarding its outcome or cancellation."""
        tool = self.get(name)
        if tool is None:
            return ToolOutcome(f"Unknown tool: {name}", "unknown_tool")
        if not isinstance(args, dict):
            return ToolOutcome("Error: Tool arguments must be an object", "invalid_arguments")
        try:
            schema = tool.input_schema
            required = schema.get("required", [])
            missing = [key for key in required if key not in args]
            if missing:
                return ToolOutcome(f"Error: Missing required arguments: {', '.join(missing)}", "invalid_arguments")
            if schema.get("additionalProperties") is False:
                unexpected = args.keys() - schema.get("properties", {}).keys()
                if unexpected:
                    return ToolOutcome(f"Error: Unexpected arguments: {', '.join(sorted(unexpected))}", "invalid_arguments")
            tool.validate_arguments(args)
        except (TypeError, ValueError) as exc:
            return ToolOutcome(f"Error: {exc}", "invalid_arguments")
        try:
            return await tool.execute_result(**args)
        except RuntimeBudgetExceeded:
            raise
        except TimeoutError as exc:
            return ToolOutcome(f"Error: {exc}", "timeout")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
