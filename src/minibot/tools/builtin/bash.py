"""Bash tool implementation."""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from ..base import BaseTool


class BashTool(BaseTool):
    """Execute shell commands."""

    name = "bash"
    description = "Run shell command."

    DANGEROUS_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]

    def __init__(self, workdir: Path, timeout: int = 60):
        self.workdir = workdir
        self.timeout = timeout

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
        # Safety check
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in command:
                return f"Error: Dangerous command pattern detected: {pattern}"

        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    command,
                    shell=True,
                    cwd=self.workdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                ),
            )
            output = (result.stdout + result.stderr).strip()
            return output[:50000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {self.timeout}s"
        except Exception as e:
            return f"Error: {e}"
