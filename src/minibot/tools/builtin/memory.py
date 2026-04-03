"""Structured memory tools for Minibot."""

from __future__ import annotations

import json
from typing import Any

from ...memory.manager import MemoryManager
from ...tools.base import BaseTool


class ReadMemoryTool(BaseTool):
    name = "read_memory"
    description = "Read a memory node or system view by URI."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Memory URI, for example memory://characters/ali or system://boot."},
            },
            "required": ["uri"],
        }

    async def execute(self, **kwargs) -> str:
        return self.memory_manager.read(kwargs["uri"])


class CreateMemoryTool(BaseTool):
    name = "create_memory"
    description = "Create a folder or memory node inside the active namespace."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "parent_uri": {"type": ["string", "null"]},
                "title": {"type": "string"},
                "kind": {"type": "string", "enum": ["folder", "memory"]},
                "slug": {"type": "string"},
                "node_type": {"type": "string"},
                "content": {"type": "string"},
                "is_core": {"type": "boolean"},
                "priority": {"type": "integer"},
            },
            "required": ["parent_uri", "title", "kind"],
        }

    async def execute(self, **kwargs) -> str:
        node = self.memory_manager.create_memory(
            parent_uri=kwargs.get("parent_uri"),
            title=kwargs["title"],
            kind=kwargs["kind"],
            slug=kwargs.get("slug"),
            node_type=kwargs.get("node_type"),
            content=kwargs.get("content", ""),
            is_core=bool(kwargs.get("is_core", False)),
            priority=int(kwargs.get("priority", 0)),
        )
        return f"Created {node.kind} at {node.uri}"


class UpdateMemoryTool(BaseTool):
    name = "update_memory"
    description = "Update memory node metadata or replace full content."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
                "title": {"type": "string"},
                "slug": {"type": "string"},
                "content": {"type": "string"},
                "node_type": {"type": "string"},
                "is_core": {"type": "boolean"},
                "priority": {"type": "integer"},
                "parent_uri": {"type": ["string", "null"]},
            },
            "required": ["uri"],
        }

    async def execute(self, **kwargs) -> str:
        uri = kwargs.pop("uri")
        node = self.memory_manager.update_memory(uri, **kwargs)
        return f"Updated {node.uri}"


class EditMemoryTool(BaseTool):
    name = "edit_memory"
    description = "Edit memory content locally via replace_text or append_text."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
                "operation": {"type": "string", "enum": ["replace_text", "append_text"]},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["uri", "operation"],
        }

    async def execute(self, **kwargs) -> str:
        uri = kwargs.pop("uri")
        node = self.memory_manager.edit_memory(uri, **kwargs)
        return f"Edited {node.uri}"


class DeleteMemoryTool(BaseTool):
    name = "delete_memory"
    description = "Delete a memory node by URI."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uri": {"type": "string"}},
            "required": ["uri"],
        }

    async def execute(self, **kwargs) -> str:
        self.memory_manager.delete_memory(kwargs["uri"])
        return f"Deleted {kwargs['uri']}"


class SearchMemoryTool(BaseTool):
    name = "search_memory"
    description = "Search memory nodes inside the active namespace."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text matched against title, URI, content, and triggers."},
                "limit": {"type": "integer", "description": "Maximum number of results to return."},
                "node_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional filters. Values may match node kinds like `memory` or `folder`, or node_type labels like `character` or `location`.",
                },
                "include_folders": {
                    "type": "boolean",
                    "description": "When true, folder nodes may appear in results. When false, folders are excluded even if filters mention `folder`.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> str:
        results = self.memory_manager.search(
            kwargs["query"],
            limit=int(kwargs.get("limit", 8)),
            node_types=kwargs.get("node_types"),
            include_folders=bool(kwargs.get("include_folders", False)),
        )
        payload = [
            {
                "uri": result.uri,
                "title": result.title,
                "kind": result.kind,
                "node_type": result.node_type,
                "matched_by": result.matched_by,
                "snippet": result.snippet,
                "parent_uri": result.parent_uri,
            }
            for result in results
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)


class ManageTriggersTool(BaseTool):
    name = "manage_triggers"
    description = "Add or remove glossary triggers for a memory node."

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
                "add": {"type": "array", "items": {"type": "string"}},
                "remove": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["uri"],
        }

    async def execute(self, **kwargs) -> str:
        payload = self.memory_manager.manage_triggers(
            kwargs["uri"],
            add=kwargs.get("add"),
            remove=kwargs.get("remove"),
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)
