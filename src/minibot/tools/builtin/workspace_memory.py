"""Agent tools for file memory and embedded Novel Memo."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from ...memory.remote_novelmemo import RemoteNovelMemoWorkspace
from ...memory.workspace import FileMemoryWorkspace, NovelMemoWorkspace, call_memory_operation
from ..function import FunctionTool

FileMemoryType = Literal["long_term", "daily"]
FileWriteMode = Literal["append", "replace", "overwrite"]


def _file_path(memory_type: FileMemoryType, date: str | None) -> str:
    if memory_type == "long_term":
        return "LONG_TERM.md"
    if memory_type == "daily":
        selected_date = date if date is not None else datetime.now().date().isoformat()
        return f"daily/{selected_date}.md"
    raise ValueError("memory_type must be 'long_term' or 'daily'.")


def file_memory_tools(workspace: FileMemoryWorkspace) -> tuple[FunctionTool, ...]:
    def memory_read(memory_type: FileMemoryType, date: str | None = None) -> str:
        """Read a long-term or daily memory note. The daily date is YYYY-MM-DD and defaults to today."""
        path = _file_path(memory_type, date)
        return workspace.document(path)["content"] or f"No memory has been written to {path}."

    def memory_list_daily() -> list[str]:
        """List daily memory dates, most recent first."""
        return [entry["title"] for entry in workspace.directory("daily")["entries"]]

    def memory_write(
        memory_type: FileMemoryType,
        content: str | None = None,
        mode: FileWriteMode = "append",
        old: str | None = None,
        new: str | None = None,
        date: str | None = None,
    ) -> str:
        """Write a memory note. Append with content, replace old with new, or overwrite with full content."""
        return workspace.edit_file(
            _file_path(memory_type, date), operation=mode, text=content, old_text=old, new_text=new,
        )

    return (
        FunctionTool(memory_read),
        FunctionTool(memory_list_daily),
        FunctionTool(memory_write),
    )


def novelmemo_tools(workspace: NovelMemoWorkspace | RemoteNovelMemoWorkspace) -> tuple[FunctionTool, ...]:
    async def read_memory(uri: str) -> str:
        """Read a Novel Memo URI or system view such as system://boot or system://index/core."""
        return await call_memory_operation(workspace.read_memory, uri)

    async def search_memory(query: str, domain: str | None = None, limit: int = 10) -> list[dict]:
        """Search the active Novel Memo Vault. Optionally filter by domain."""
        return await call_memory_operation(workspace.search_memory, query, domain=domain, limit=limit)

    async def create_memory(parent_uri: str, content: str, disclosure: str, title: str | None = None) -> str:
        """Create a child note. Disclosure describes when this note should be read."""
        uri = await call_memory_operation(workspace.create_memory, parent_uri, content, disclosure, title)
        return f"Created {uri}"

    async def update_memory(
        uri: str,
        old_string: str | None = None,
        new_string: str | None = None,
        append: str | None = None,
        disclosure: str | None = None,
    ) -> str:
        """Update a note using a text replacement, an append, or a disclosure change."""
        updated = await call_memory_operation(
            workspace.update_memory, uri,
            old_string=old_string, new_string=new_string, append=append, disclosure=disclosure,
        )
        return f"Updated {updated}"

    async def delete_memory(uri: str) -> str:
        """Delete a note from the active Novel Memo Vault."""
        deleted = await call_memory_operation(workspace.delete_memory, uri)
        return f"Deleted {deleted}"

    async def manage_triggers(uri: str, add: list[str] | None = None, remove: list[str] | None = None) -> list[str]:
        """Add or remove trigger terms for a Novel Memo note."""
        return await call_memory_operation(workspace.manage_triggers, uri, add=add, remove=remove)

    return (
        FunctionTool(read_memory),
        FunctionTool(search_memory),
        FunctionTool(create_memory),
        FunctionTool(update_memory),
        FunctionTool(delete_memory),
        FunctionTool(manage_triggers),
    )
