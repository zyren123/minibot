"""Data models for agent teams."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    """UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class MemberStatus(str, Enum):
    """Team member lifecycle status."""

    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    STOPPED = "stopped"
    ERROR = "error"


class TaskStatus(str, Enum):
    """Task lifecycle status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TeamMember:
    """Single team member."""

    member_id: str
    name: str
    role: str  # lead | teammate
    status: MemberStatus = MemberStatus.IDLE
    created_at: str = field(default_factory=utc_now_iso)
    last_active_at: str = field(default_factory=utc_now_iso)
    last_error: str | None = None


@dataclass
class Team:
    """In-memory team definition."""

    team_id: str
    name: str
    lead_id: str
    created_at: str = field(default_factory=utc_now_iso)
    members: dict[str, TeamMember] = field(default_factory=dict)


@dataclass
class TeamMessage:
    """Message between team members."""

    message_id: str
    team_id: str
    sender_id: str
    recipient_id: str
    content: str
    created_at: str = field(default_factory=utc_now_iso)
    kind: str = "direct"  # direct | broadcast


@dataclass
class TeamTask:
    """Shared task-board item."""

    task_id: str
    team_id: str
    title: str
    details: str
    created_by: str
    created_at: str = field(default_factory=utc_now_iso)
    status: TaskStatus = TaskStatus.PENDING
    assignee_id: str | None = None
    completed_by: str | None = None
    completed_at: str | None = None
    completion_note: str | None = None


@dataclass
class FileLock:
    """File-level lock held by a teammate."""

    path: str
    owner_member_id: str
    acquired_at: str = field(default_factory=utc_now_iso)


def as_dict(obj: Any) -> dict[str, Any]:
    """Dataclass to plain dict helper."""
    from dataclasses import asdict

    return asdict(obj)
