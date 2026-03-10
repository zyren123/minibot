"""Subagent type registry."""

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.schema import SubagentsConfig


@dataclass
class AgentType:
    """Definition of a subagent type."""
    name: str
    description: str
    tools: list[str] | Literal["*"]
    prompt: str
    skills_enabled: bool = False
    enabled: bool = True


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

    DEFAULT_AGENT_NAMES: frozenset[str] = frozenset(DEFAULT_AGENTS.keys())

    def __init__(self):
        self._agents: dict[str, AgentType] = dict(self.DEFAULT_AGENTS)

    def apply_config(self, config: "SubagentsConfig") -> None:
        """Apply configuration overrides to registered agents."""
        for name, agent_cfg in config.agents.items():
            agent = self._agents.get(name)
            if agent is None:
                # Register custom agent from config if it has enough info
                if agent_cfg.description is not None and agent_cfg.prompt is not None:
                    agent = AgentType(
                        name=name,
                        description=agent_cfg.description,
                        tools=agent_cfg.tools or ["bash", "read_file"],
                        prompt=agent_cfg.prompt,
                        skills_enabled=agent_cfg.skills_enabled,
                        enabled=agent_cfg.enabled,
                    )
                    self._agents[name] = agent
                continue
            if agent_cfg.description is not None:
                agent.description = agent_cfg.description
            if agent_cfg.tools is not None:
                agent.tools = agent_cfg.tools
            if agent_cfg.prompt is not None:
                agent.prompt = agent_cfg.prompt
            agent.skills_enabled = agent_cfg.skills_enabled
            agent.enabled = agent_cfg.enabled

    def register(self, agent: AgentType) -> None:
        """Register an agent type."""
        self._agents[agent.name] = agent

    def unregister(self, name: str) -> bool:
        """Remove a non-default agent. Returns True if removed."""
        if name in self.DEFAULT_AGENT_NAMES:
            return False
        return self._agents.pop(name, None) is not None

    def enable(self, name: str) -> bool:
        """Enable an agent. Returns True if found."""
        agent = self._agents.get(name)
        if agent is None:
            return False
        agent.enabled = True
        return True

    def disable(self, name: str) -> bool:
        """Disable an agent. Returns True if found."""
        agent = self._agents.get(name)
        if agent is None:
            return False
        agent.enabled = False
        return True

    def get(self, name: str) -> AgentType | None:
        """Get an agent type by name."""
        return self._agents.get(name)

    def list_all(self) -> list[AgentType]:
        """List all agent types with full details."""
        return list(self._agents.values())

    def list_names(self) -> list[str]:
        """List all agent type names."""
        return list(self._agents.keys())

    def get_descriptions(self) -> str:
        """Get formatted descriptions for system prompt (enabled only)."""
        return "\n".join(
            f"- {name}: {agent.description}"
            for name, agent in self._agents.items()
            if agent.enabled
        )

    def to_config_dict(self) -> dict[str, dict]:
        """Serialize all agents to a dict suitable for YAML write-back."""
        result: dict[str, dict] = {}
        for name, agent in self._agents.items():
            entry: dict = {"enabled": agent.enabled, "skills_enabled": agent.skills_enabled}
            if name not in self.DEFAULT_AGENT_NAMES:
                entry["description"] = agent.description
                entry["tools"] = agent.tools
                entry["prompt"] = agent.prompt
            elif agent.description != self.DEFAULT_AGENTS.get(name, agent).description:
                entry["description"] = agent.description
            result[name] = entry
        return result

