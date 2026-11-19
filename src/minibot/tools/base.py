"""Base tool class."""

from abc import ABC, abstractmethod
import inspect
from typing import Any

from ..core.types import ToolDefinition
from .result import ToolOutcome


class BaseTool(ABC):
    """Abstract base class for all tools."""

    name: str
    description: str

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """Return the JSON schema for tool input."""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """Execute the tool with given arguments."""
        pass

    def validate_arguments(self, args: dict[str, Any]) -> None:
        inspect.signature(self.execute).bind(**args)

    async def execute_result(self, **kwargs) -> ToolOutcome:
        """Adapt existing text-returning tools without interpreting their text."""
        return ToolOutcome(await self.execute(**kwargs))

    def get_definition(self) -> ToolDefinition:
        """Get tool definition for API."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    def to_dict(self) -> dict:
        """Convert to API-compatible dict."""
        return self.get_definition().to_dict()


class ResultTool(BaseTool):
    """Built-in tools with explicit outcomes and a compatible text entrypoint."""

    async def execute(self, *args, **kwargs) -> str:
        return (await self.execute_result(*args, **kwargs)).text

    def validate_arguments(self, args: dict[str, Any]) -> None:
        inspect.signature(self.execute_result).bind(**args)

    @abstractmethod
    async def execute_result(self, **kwargs) -> ToolOutcome:
        pass
