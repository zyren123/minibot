"""Type definitions for Minibot."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ContentBlock:
    """A content block in a message."""
    type: Literal["text", "tool_use", "tool_result"]
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: str | None = None


@dataclass
class Message:
    """A message in the conversation."""
    role: Literal["user", "assistant", "system"]
    content: str | list[ContentBlock | dict]


@dataclass
class ToolCall:
    """A tool call from the model."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ToolDefinition:
    """Definition of a tool for the API."""
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict:
        """Convert to OpenAI/GLM API-compatible dict."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
