"""Task tool for spawning subagents."""

from typing import Any, TYPE_CHECKING
from collections.abc import Callable

from ..base import ResultTool
from ..result import ToolOutcome

if TYPE_CHECKING:
    from ...subagents.executor import SubagentExecutor
    from ...subagents.registry import AgentRegistry
    from ...traces import RunTrace


class TaskTool(ResultTool):
    """Spawn a subagent for a focused subtask."""

    name = "Task"

    def __init__(
        self,
        agent_registry: "AgentRegistry",
        subagent_executor: "SubagentExecutor",
        *,
        trace_for_run: "Callable[[], RunTrace | None] | None" = None,
    ):
        self.agent_registry = agent_registry
        self.subagent_executor = subagent_executor
        self.trace_for_run = trace_for_run

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
                    "enum": self.agent_registry.list_enabled_names(),
                },
            },
            "required": ["description", "prompt", "agent_type"],
        }

    async def execute_result(
        self,
        description: str,
        prompt: str,
        agent_type: str,
    ) -> ToolOutcome:
        """Execute a subagent task."""
        agent = self.agent_registry.get(agent_type)
        if agent is None:
            return ToolOutcome(f"Error: Unknown agent type '{agent_type}'", "invalid_arguments")
        if not agent.enabled:
            return ToolOutcome(f"Error: Subagent '{agent_type}' is disabled", "refused")
        trace = self.trace_for_run() if self.trace_for_run is not None else None
        return ToolOutcome(await self.subagent_executor.execute(description, prompt, agent_type, trace=trace))
