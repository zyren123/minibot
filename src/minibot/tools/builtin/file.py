"""File operation tools."""

import asyncio
from pathlib import Path
from typing import Any

from ..base import ResultTool
from ..result import ToolOutcome
from ...teams.context import get_team_execution_context


def safe_path(workdir: Path, path: str) -> Path:
    """Ensure path stays within workspace."""
    resolved = (workdir / path).resolve()
    if not resolved.is_relative_to(workdir):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


class ReadFileTool(ResultTool):
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

    async def execute_result(self, path: str, limit: int | None = None) -> ToolOutcome:
        """Read a file."""
        try:
            fp = safe_path(self.workdir, path)
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, lambda: fp.read_text(encoding="utf-8"))
            lines = content.splitlines()
            if limit:
                lines = lines[:limit]
            return ToolOutcome("\n".join(lines)[:50000], "success")
        except Exception as e:
            return ToolOutcome(f"Error: {e}", "error")


class WriteFileTool(ResultTool):
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

    async def execute_result(self, path: str, content: str) -> ToolOutcome:
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
                    return ToolOutcome(
                        f"Error: File lock conflict for '{path}'. Current owner: {owner}",
                        "refused",
                    )

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: fp.write_text(content, encoding="utf-8"))
            return ToolOutcome(f"Wrote {len(content)} bytes to {path}", "success")
        except Exception as e:
            return ToolOutcome(f"Error: {e}", "error")


class EditFileTool(ResultTool):
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

    async def execute_result(self, path: str, old_text: str, new_text: str) -> ToolOutcome:
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
                    return ToolOutcome(
                        f"Error: File lock conflict for '{path}'. Current owner: {owner}",
                        "refused",
                    )

            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, lambda: fp.read_text(encoding="utf-8"))

            if old_text not in content:
                return ToolOutcome(f"Error: Text not found in {path}", "error")

            new_content = content.replace(old_text, new_text, 1)
            await loop.run_in_executor(None, lambda: fp.write_text(new_content, encoding="utf-8"))
            return ToolOutcome(f"Edited {path}", "success")
        except Exception as e:
            return ToolOutcome(f"Error: {e}", "error")
