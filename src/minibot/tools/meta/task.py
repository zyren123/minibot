"""Task tool for spawning subagents."""

from typing import Any, TYPE_CHECKING

from ..base import BaseTool

if TYPE_CHECKING:
    from ...subagents.executor import SubagentExecutor
    from ...subagents.registry import AgentRegistry


class TaskTool(BaseTool):
    """Spawn a subagent for a focused subtask."""

    name = "Task"

    def __init__(
        self,
        agent_registry: "AgentRegistry",
        subagent_executor: "SubagentExecutor",
    ):
        self.agent_registry = agent_registry
        self.subagent_executor = subagent_executor

    @property
    def description(self) -> str:
        agents_desc = self.agent_registry.get_descriptions()
        return f"""Spawn a subagent for a focused subtask.

Agent types:
{agents_desc}"""

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short task description (3-5 words)",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed instructions for the subagent",
                },
                "agent_type": {
                    "type": "string",
                    "enum": self.agent_registry.list_names(),
                },
            },
            "required": ["description", "prompt", "agent_type"],
        }

    async def execute(
        self,
        description: str,
        prompt: str,
        agent_type: str,
    ) -> str:
        """Execute a subagent task."""
        return await self.subagent_executor.execute(description, prompt, agent_type)
