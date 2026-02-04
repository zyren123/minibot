"""Base tool class."""

from abc import ABC, abstractmethod
from typing import Any

from ..core.types import ToolDefinition


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
