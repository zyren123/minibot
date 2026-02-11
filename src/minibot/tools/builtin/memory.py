"""Memory read/write tools for Minibot."""

from typing import Any

from ...tools.base import BaseTool
from ...memory.manager import MemoryManager


class MemoryReadTool(BaseTool):
    """Tool for reading memory content."""

    name = "memory_read"
    description = "Read memory content (long-term or daily). Use this to recall project knowledge, user preferences, or previous session context."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "enum": ["long_term", "daily"],
                    "description": "Type of memory to read.",
                },
                "date": {
                    "type": "string",
                    "description": "Date for daily memory (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["memory_type"],
        }

    async def execute(self, **kwargs) -> str:
        memory_type = kwargs["memory_type"]
        date = kwargs.get("date")

        if memory_type == "long_term":
            content = self.memory_manager.read_long_term()
            return content if content else "(Long-term memory is empty)"
        elif memory_type == "daily":
            try:
                content = self.memory_manager.read_daily(date)
            except ValueError as exc:
                return f"Invalid date: {exc}"
            label = date or "today"
            return content if content else f"(Daily memory for {label} is empty)"
        else:
            return f"Unknown memory_type: {memory_type}. Use 'long_term' or 'daily'."


class MemoryWriteTool(BaseTool):
    """Tool for writing memory content."""

    name = "memory_write"
    description = "Write or append to memory. Use 'long_term' for stable project knowledge, conventions, and user preferences. Use 'daily' for today's progress, context, and continuations."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "enum": ["long_term", "daily"],
                    "description": "Type of memory to write.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (ignored for mode 'replace').",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append", "replace"],
                    "description": "Write mode. Defaults to 'append'. Use 'replace' with 'old' and 'new'.",
                },
                "old": {
                    "type": "string",
                    "description": "Text to find when mode is 'replace'.",
                },
                "new": {
                    "type": "string",
                    "description": "Replacement text when mode is 'replace'.",
                },
                "date": {
                    "type": "string",
                    "description": "Date for daily memory (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["memory_type"],
        }

    async def execute(self, **kwargs) -> str:
        memory_type = kwargs["memory_type"]
        content = kwargs.get("content")
        mode = kwargs.get("mode", "append")
        date = kwargs.get("date")
        old = kwargs.get("old")
        new = kwargs.get("new")

        if mode != "replace" and content is None:
            return "Invalid write: 'content' is required unless mode is 'replace'."

        if memory_type == "long_term":
            if mode == "replace":
                if not old:
                    return "Invalid replace: 'old' must be non-empty."
                if new is None:
                    return "Invalid replace: 'new' is required."
                return self.memory_manager.replace_long_term(old, new)
            if mode == "append":
                return self.memory_manager.append_long_term(content)
            return self.memory_manager.write_long_term(content)
        elif memory_type == "daily":
            try:
                if mode == "replace":
                    if not old:
                        return "Invalid replace: 'old' must be non-empty."
                    if new is None:
                        return "Invalid replace: 'new' is required."
                    return self.memory_manager.replace_daily(old, new, date)
                if mode == "append":
                    return self.memory_manager.append_daily(content, date)
                return self.memory_manager.write_daily(content, date)
            except ValueError as exc:
                return f"Invalid date: {exc}"
        else:
            return f"Unknown memory_type: {memory_type}. Use 'long_term' or 'daily'."
