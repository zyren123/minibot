"""File operation tools."""

import json
from pathlib import Path
from typing import Any

from ..base import ResultTool
from ..result import ToolOutcome
from ...core.artifacts import current_artifacts
from ...file_checkpoints import GitFileCheckpoints
from ...teams.context import get_team_execution_context
from .workspace_backend import LocalBackend, WorkspaceBackend, WorkspacePath
from .workspace_files import FileOutputLimit


class WorkspaceFileTool(ResultTool):
    def __init__(
        self, workdir: Path, *, checkpoints: GitFileCheckpoints | None = None, backend: WorkspaceBackend | None = None,
    ):
        self.workdir = workdir
        self.backend = backend if backend is not None else LocalBackend(workdir, checkpoints=checkpoints)

    def permission_target(self, path: str) -> WorkspacePath:
        return self.backend.target(path)

    async def _file_lock_conflict(self, path: str, target: WorkspacePath) -> ToolOutcome | None:
        team_ctx = get_team_execution_context()
        if team_ctx is None or team_ctx.role != "teammate" or team_ctx.runtime is None or not team_ctx.member_id:
            return None
        if target.host_path is None:
            return ToolOutcome("Error: Team file locks require a trusted local workspace", "refused")
        ok, owner = await team_ctx.runtime.acquire_file_lock(member_id=team_ctx.member_id, path=str(target.host_path))
        if not ok:
            return ToolOutcome(f"Error: File lock conflict for '{path}'. Current owner: {owner}", "refused")
        return None


class ReadFileTool(WorkspaceFileTool):
    """Read file contents."""

    name = "read_file"
    description = "Read file contents."

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
        scope = current_artifacts.get()
        try:
            text = await self.backend.read_file(path, limit=limit, max_bytes=scope.capture_bytes if scope else None)
            if scope is not None and len(text) <= scope.threshold_chars:
                text = "\n".join(text.splitlines())
            return ToolOutcome(text[:50000] if scope is None else text, "success")
        except FileOutputLimit as exc:
            if scope is None:
                return ToolOutcome(f"Error: {exc}", "error", artifact_eligible=False)
            detail = f"File output exceeds the {scope.store.max_bytes}-byte artifact limit."
            return ToolOutcome(scope.failure(detail), "error", artifact_eligible=False)
        except TimeoutError:
            return ToolOutcome("Error: File read timed out", "timeout")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class WriteFileTool(WorkspaceFileTool):
    """Write content to file."""

    name = "write_file"
    description = "Write to file."

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
            target = self.backend.target(path)
            conflict = await self._file_lock_conflict(path, target)
            if conflict is not None:
                return conflict
            await self.backend.write_file(target, content)
            return ToolOutcome(f"Wrote {len(content)} bytes to {path}", "success")
        except TimeoutError:
            return ToolOutcome("Error: File write timed out", "timeout")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class EditFileTool(WorkspaceFileTool):
    """Replace text in file."""

    name = "edit_file"
    description = "Replace text in file."

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
            target = self.backend.target(path)
            conflict = await self._file_lock_conflict(path, target)
            if conflict is not None:
                return conflict
            if not await self.backend.edit_file(target, old_text, new_text):
                return ToolOutcome(f"Error: Text not found in {path}", "error")
            return ToolOutcome(f"Edited {path}", "success")
        except TimeoutError:
            return ToolOutcome("Error: File edit timed out", "timeout")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class ListDirTool(WorkspaceFileTool):
    """List immediate workspace directory entries."""

    name = "list_dir"
    description = "List the names in a directory. Use '.' for the workspace root."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the directory to list"}},
            "required": ["path"],
        }

    async def execute_result(self, path: str) -> ToolOutcome:
        try:
            names = await self.backend.list_dir(path)
            return ToolOutcome(json.dumps(names, ensure_ascii=True), "success")
        except TimeoutError:
            return ToolOutcome("Error: Directory listing timed out", "timeout")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")
