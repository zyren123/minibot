"""Task tool for spawning subagents."""

from typing import Any, TYPE_CHECKING
from collections.abc import Callable

from ...core.artifacts import MAX_READ_CHARS
from ..base import ResultTool
from ..result import ToolOutcome

if TYPE_CHECKING:
    from ...subagents.executor import SubagentExecutor
    from ...subagents.registry import AgentRegistry
    from ...traces import RunTrace


MAX_BATCH_CHILDREN = 4


def _task_schema(agent_registry: "AgentRegistry") -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short task description (3-5 words)"},
            "prompt": {"type": "string", "description": "Detailed instructions for the subagent"},
            "agent_type": {"type": "string", "enum": agent_registry.list_enabled_names()},
        },
        "required": ["description", "prompt", "agent_type"],
    }


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
        return _task_schema(self.agent_registry)

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


class JournalTool(ResultTool):
    """A tool that can only execute through the durable run dispatcher."""

    async def execute_result(self, **kwargs: Any) -> ToolOutcome:
        return ToolOutcome("This tool requires an active durable parent run.", "refused")


class DurableTaskTool(JournalTool):
    name = "Task"
    description = "Delegate to an isolated Docker child and collect its declared UTF-8 files."

    def __init__(self, agent_registry: "AgentRegistry"):
        self.agent_registry = agent_registry

    @property
    def input_schema(self) -> dict[str, Any]:
        schema = _task_schema(self.agent_registry)
        schema["properties"]["deliverables"] = {
            "type": "array", "items": {"type": "string"},
            "description": "Workspace-relative file paths the child should return.",
        }
        schema["additionalProperties"] = False
        return schema


class TaskBatchTool(DurableTaskTool):
    name = "TaskBatch"
    description = "Run independent Docker children concurrently. Specify dependent work in later Task calls."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"tasks": {
                "type": "array", "minItems": 1, "maxItems": MAX_BATCH_CHILDREN,
                "items": super().input_schema,
            }},
            "required": ["tasks"],
            "additionalProperties": False,
        }


class ReadChildArtifactTool(JournalTool):
    name = "ReadChildArtifact"
    description = "Read verified text from a child deliverable belonging to this parent run."

    def __init__(self, output_chars: int):
        self.limit = min(output_chars, MAX_READ_CHARS)

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "child_run_id": {"type": "string"},
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": self.limit, "default": self.limit},
            },
            "required": ["child_run_id", "path"],
            "additionalProperties": False,
        }
