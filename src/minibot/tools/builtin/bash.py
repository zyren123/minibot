"""Bash tool implementation."""

from pathlib import Path
import math
from typing import Any

from ..base import ResultTool
from ..result import ToolOutcome
from ...config.schema import BashConfig
from ...core.artifacts import current_artifacts
from .workspace_backend import LocalBackend, WorkspaceBackend
from ...teams.context import get_team_execution_context


class BashTool(ResultTool):
    """Execute shell commands."""

    name = "bash"
    description = "Run shell command."

    DANGEROUS_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]

    def __init__(
        self, workdir: Path, timeout: int | float = 60, *,
        config: BashConfig | None = None, backend: WorkspaceBackend | None = None,
    ):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self.workdir = workdir
        self.timeout = timeout
        self.config = config or BashConfig()
        self._backend = backend if backend is not None else LocalBackend(workdir, bash=self.config)

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                }
            },
            "required": ["command"],
        }

    async def execute_result(self, command: str) -> ToolOutcome:
        """Execute a shell command."""
        if self.config.backend == "disabled":
            return ToolOutcome("Error: Bash execution is disabled", "refused")
        # Safety check
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in command:
                return ToolOutcome(f"Error: Dangerous command pattern detected: {pattern}", "refused")

        try:
            team_ctx = get_team_execution_context()
            if (
                team_ctx is not None
                and team_ctx.role == "teammate"
                and team_ctx.runtime is not None
                and team_ctx.member_id
            ):
                ok, owner = await team_ctx.runtime.acquire_workspace_lock(
                    member_id=team_ctx.member_id
                )
                if not ok:
                    return ToolOutcome(
                        "Error: Workspace lock conflict for bash execution. "
                        f"Current owner: {owner}",
                        "refused",
                    )

            scope = current_artifacts.get()
            output_limit = scope.capture_bytes if scope is not None else None
            result = await self._backend.run(command, timeout=self.timeout, output_limit=output_limit)
            if scope is not None and (result.output_exceeded or result.incomplete_output):
                detail = (
                    f"Output exceeds the {scope.store.max_bytes}-byte artifact limit."
                    if result.output_exceeded else "Output collection was incomplete."
                )
                return ToolOutcome(scope.failure(detail), "error", exit_code=result.exit_code, artifact_eligible=False)

            raw = result.stdout + result.stderr
            prefix = f"Error: Command exited with code {result.exit_code}\n" if result.exit_code else ""
            if scope is None:
                output = (prefix + (raw.strip() or "(no output)"))[:50000]
            elif len(prefix) + len(raw) > scope.threshold_chars:
                output = prefix + raw
            else:
                output = prefix + (raw.strip() or "(no output)")
            return ToolOutcome(
                output, "error" if result.exit_code else "success", exit_code=result.exit_code,
            )
        except TimeoutError:
            return ToolOutcome(f"Error: Command timed out after {self.timeout}s", "timeout")
        except Exception as e:
            return ToolOutcome(f"Error: {e}", "error")
