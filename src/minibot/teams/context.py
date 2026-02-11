"""Context propagation for team-aware tool execution."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import TeamRuntime


@dataclass
class TeamExecutionContext:
    """Per-execution team context available to tools."""

    role: str
    team_id: str | None
    member_id: str | None
    runtime: "TeamRuntime | None" = None


_CTX: ContextVar[TeamExecutionContext | None] = ContextVar(
    "minibot_team_execution_context",
    default=None,
)


@contextmanager
def team_execution_context(ctx: TeamExecutionContext | None) -> Iterator[None]:
    """Temporarily bind execution context for the current task."""
    token = _CTX.set(ctx)
    try:
        yield
    finally:
        _CTX.reset(token)


def get_team_execution_context() -> TeamExecutionContext | None:
    """Get the current team execution context."""
    return _CTX.get()
