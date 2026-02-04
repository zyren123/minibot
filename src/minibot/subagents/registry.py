"""Subagent type registry."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentType:
    """Definition of a subagent type."""
    name: str
    description: str
    tools: list[str] | Literal["*"]
    prompt: str


class AgentRegistry:
    """Registry for subagent types."""

    DEFAULT_AGENTS = {
        "explore": AgentType(
            name="explore",
            description="Read-only agent for exploring code, finding files, searching",
            tools=["bash", "read_file"],
            prompt="You are an exploration agent. Search and analyze, but never modify files. Return a concise summary.",
        ),
        "code": AgentType(
            name="code",
            description="Full agent for implementing features and fixing bugs",
            tools="*",
            prompt="You are a coding agent. Implement the requested changes efficiently.",
        ),
        "plan": AgentType(
            name="plan",
            description="Planning agent for designing implementation strategies",
            tools=["bash", "read_file"],
            prompt="You are a planning agent. Analyze the codebase and output a numbered implementation plan. Do NOT make changes.",
        ),
    }

    def __init__(self):
        self._agents: dict[str, AgentType] = dict(self.DEFAULT_AGENTS)

    def register(self, agent: AgentType) -> None:
        """Register an agent type."""
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentType | None:
        """Get an agent type by name."""
        return self._agents.get(name)

    def list_names(self) -> list[str]:
        """List all agent type names."""
        return list(self._agents.keys())

    def get_descriptions(self) -> str:
        """Get formatted descriptions for system prompt."""
        return "\n".join(
            f"- {name}: {agent.description}"
            for name, agent in self._agents.items()
        )
