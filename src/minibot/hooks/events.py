"""Hook event definitions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookEvent(Enum):
    """Events that can trigger hooks."""

    # Tool lifecycle
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"

    # Agent turn lifecycle
    PRE_AGENT_TURN = "pre_agent_turn"
    POST_AGENT_TURN = "post_agent_turn"

    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Skill loading
    SKILL_LOAD = "skill_load"

    # Team lifecycle
    TEAMMATE_IDLE = "teammate_idle"
    TASK_COMPLETED = "task_completed"


@dataclass
class HookContext:
    """Base context for hook execution."""

    event: HookEvent
    workdir: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallContext(HookContext):
    """Context for tool call hooks."""

    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: str | None = None
    blocked: bool = False
    block_reason: str | None = None


@dataclass
class SessionContext(HookContext):
    """Context for session hooks."""

    session_id: str = ""
    user_message: str | None = None


@dataclass
class SkillLoadContext(HookContext):
    """Context for skill loading hooks."""

    skill_name: str = ""
    skill_content: str | None = None


@dataclass
class TeamEventContext(HookContext):
    """Context for team-related hooks."""

    team_member_id: str | None = None
    task_id: str | None = None
    detail: str | None = None


@dataclass
class HookResult:
    """Result of hook execution."""

    success: bool
    output: str | None = None
    error: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    modified_context: dict[str, Any] | None = None
