"""Subagent system."""

from .registry import AgentRegistry, AgentType
from .executor import SubagentExecutor

__all__ = ["AgentRegistry", "AgentType", "SubagentExecutor"]
