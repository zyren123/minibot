"""File operation tools."""

import asyncio
from pathlib import Path
from typing import Any

from ..base import BaseTool
from ...teams.context import get_team_execution_context


def safe_path(workdir: Path, path: str) -> Path:
    """Ensure path stays within workspace."""
    resolved = (workdir / path).resolve()
    if not resolved.is_relative_to(workdir):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


class ReadFileTool(BaseTool):
    """Read file contents."""

    name = "read_file"
    description = "Read file contents."

    def __init__(self, workdir: Path):
        self.workdir = workdir

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, limit: int | None = None) -> str:
        """Read a file."""
        try:
            fp = safe_path(self.workdir, path)
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, fp.read_text)
            lines = content.splitlines()
            if limit:
                lines = lines[:limit]
            return "\n".join(lines)[:50000]
        except Exception as e:
            return f"Error: {e}"


class WriteFileTool(BaseTool):
    """Write content to file."""

    name = "write_file"
    description = "Write to file."

    def __init__(self, workdir: Path):
        self.workdir = workdir

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str) -> str:
        """Write content to a file."""
        try:
            fp = safe_path(self.workdir, path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            team_ctx = get_team_execution_context()
            if (
                team_ctx is not None
                and team_ctx.role == "teammate"
                and team_ctx.runtime is not None
                and team_ctx.member_id
            ):
                ok, owner = await team_ctx.runtime.acquire_file_lock(
                    member_id=team_ctx.member_id,
                    path=str(fp),
                )
                if not ok:
                    return (
                        f"Error: File lock conflict for '{path}'. "
                        f"Current owner: {owner}"
                    )

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: fp.write_text(content))
            return f"Wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"


class EditFileTool(BaseTool):
    """Replace text in file."""

    name = "edit_file"
    description = "Replace text in file."

    def __init__(self, workdir: Path):
        self.workdir = workdir

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_text": {
                    "type": "string",
                    "description": "Text to find and replace",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(self, path: str, old_text: str, new_text: str) -> str:
        """Replace text in a file."""
        try:
            fp = safe_path(self.workdir, path)
            team_ctx = get_team_execution_context()
            if (
                team_ctx is not None
                and team_ctx.role == "teammate"
                and team_ctx.runtime is not None
                and team_ctx.member_id
            ):
                ok, owner = await team_ctx.runtime.acquire_file_lock(
                    member_id=team_ctx.member_id,
                    path=str(fp),
                )
                if not ok:
                    return (
                        f"Error: File lock conflict for '{path}'. "
                        f"Current owner: {owner}"
                    )

            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, fp.read_text)

            if old_text not in content:
                return f"Error: Text not found in {path}"

            new_content = content.replace(old_text, new_text, 1)
            await loop.run_in_executor(None, lambda: fp.write_text(new_content))
            return f"Edited {path}"
        except Exception as e:
            return f"Error: {e}"
