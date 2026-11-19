"""Bash tool implementation."""

from pathlib import Path
import math
from typing import Any

from ..base import BaseTool
from ...config.schema import BashConfig
from .bash_execution import LocalBackend, DockerBackend
from ...teams.context import get_team_execution_context


class BashTool(BaseTool):
    """Execute shell commands."""

    name = "bash"
    description = "Run shell command."

    DANGEROUS_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]

    def __init__(self, workdir: Path, timeout: int | float = 60, *, config: BashConfig | None = None):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self.workdir = workdir
        self.timeout = timeout
        self.config = config or BashConfig()
        self._backend = (DockerBackend(workdir, self.config)
                         if self.config.backend == "docker" else LocalBackend(workdir))

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

    async def execute(self, command: str) -> str:
        """Execute a shell command."""
        if self.config.backend == "disabled":
            return "Error: Bash execution is disabled"
        # Safety check
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in command:
                return f"Error: Dangerous command pattern detected: {pattern}"

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
                    return (
                        "Error: Workspace lock conflict for bash execution. "
                        f"Current owner: {owner}"
                    )

            result = await self._backend.run(command, timeout=self.timeout)
            output = (result.stdout + result.stderr).strip()
            prefix = f"Error: Command exited with code {result.exit_code}\n" if result.exit_code else ""
            return (prefix + (output or "(no output)"))[:50000]
        except TimeoutError:
            return f"Error: Command timed out after {self.timeout}s"
        except Exception as e:
            return f"Error: {e}"
