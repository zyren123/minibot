"""Team orchestration tools."""

from __future__ import annotations

import json
from typing import Any

from ..base import BaseTool
from ...teams.context import TeamExecutionContext, get_team_execution_context
from ...teams.runtime import TeamRuntime


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _ctx() -> tuple[TeamExecutionContext | None, str | None]:
    ctx = get_team_execution_context()
    if ctx is None:
        return None, "Error: Team execution context missing"
    return ctx, None


def _actor_id(ctx: TeamExecutionContext) -> tuple[str | None, str | None]:
    if ctx.role == "lead":
        return "lead", None
    if ctx.member_id:
        return ctx.member_id, None
    return None, "Error: Missing member_id for teammate"


class TeamCreateTool(BaseTool):
    """Create a team and spawn teammates."""

    name = "TeamCreate"
    description = "Create an in-process teammate team."

    def __init__(
        self,
        runtime: TeamRuntime,
        *,
        default_members: int,
    ):
        self.runtime = runtime
        self.default_members = default_members

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "Team name"},
                "member_count": {
                    "type": "integer",
                    "description": "Number of teammates to spawn",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        team_name: str = "default-team",
        member_count: int | None = None,
    ) -> str:
        try:
            ctx, err = _ctx()
            if err:
                return err
            info = await self.runtime.create_team(
                actor_role=ctx.role,
                team_name=team_name,
                member_count=member_count if member_count is not None else self.default_members,
            )
            return _json(info)
        except Exception as exc:
            return f"Error: {exc}"


class TeamMembersTool(BaseTool):
    """List team members and statuses."""

    name = "TeamMembers"
    description = "List active team members and statuses."

    def __init__(self, runtime: TeamRuntime):
        self.runtime = runtime

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self) -> str:
        try:
            members = await self.runtime.list_members()
            return _json(members)
        except Exception as exc:
            return f"Error: {exc}"


class TeamTaskTool(BaseTool):
    """Operate on team task board."""

    name = "TeamTask"
    description = "Manage team tasks: create/list/assign/claim/complete."

    def __init__(
        self,
        runtime: TeamRuntime,
    ):
        self.runtime = runtime

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "assign", "claim", "complete"],
                },
                "title": {"type": "string"},
                "details": {"type": "string"},
                "task_id": {"type": "string"},
                "assignee_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        title: str = "",
        details: str = "",
        task_id: str = "",
        assignee_id: str = "",
        note: str = "",
    ) -> str:
        try:
            ctx, err = _ctx()
            if err:
                return err
            actor_role = ctx.role
            actor, err = _actor_id(ctx)
            if err:
                return err

            if action == "list":
                return _json(await self.runtime.list_tasks())

            if action == "create":
                task = await self.runtime.create_task(
                    actor_role=actor_role,
                    actor_id=actor,
                    title=title,
                    details=details,
                    assignee_id=assignee_id or None,
                )
                return _json(task)

            if action == "assign":
                task = await self.runtime.assign_task(
                    actor_role=actor_role,
                    task_id=task_id,
                    assignee_id=assignee_id,
                )
                return _json(task)

            if action == "claim":
                task = await self.runtime.claim_task(
                    actor_role=actor_role,
                    actor_id=actor,
                    task_id=task_id or None,
                )
                return _json(task)

            if action == "complete":
                task = await self.runtime.complete_task(
                    actor_role=actor_role,
                    actor_id=actor,
                    task_id=task_id,
                    note=note or None,
                )
                return _json(task)

            return f"Error: Unknown action '{action}'"
        except Exception as exc:
            return f"Error: {exc}"


class TeamMessageTool(BaseTool):
    """Send a direct team message."""

    name = "TeamMessage"
    description = "Send direct message to one team member."

    def __init__(self, runtime: TeamRuntime):
        self.runtime = runtime

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to_member_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["to_member_id", "content"],
        }

    async def execute(self, to_member_id: str, content: str) -> str:
        try:
            ctx, err = _ctx()
            if err:
                return err
            sender_id, err = _actor_id(ctx)
            if err:
                return err
            msg = await self.runtime.send_message(
                sender_id=sender_id,
                recipient_id=to_member_id,
                content=content,
            )
            return _json(msg)
        except Exception as exc:
            return f"Error: {exc}"


class TeamBroadcastTool(BaseTool):
    """Broadcast message to all team members."""

    name = "TeamBroadcast"
    description = "Broadcast message to all team members."

    def __init__(self, runtime: TeamRuntime):
        self.runtime = runtime

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
            },
            "required": ["content"],
        }

    async def execute(self, content: str) -> str:
        try:
            ctx, err = _ctx()
            if err:
                return err
            sender_id, err = _actor_id(ctx)
            if err:
                return err
            result = await self.runtime.broadcast(
                sender_id=sender_id,
                content=content,
            )
            return _json(result)
        except Exception as exc:
            return f"Error: {exc}"


class TeamWaitTool(BaseTool):
    """Wait for teammate/runtime events."""

    name = "TeamWait"
    description = "Wait for team events and return event batch."

    def __init__(self, runtime: TeamRuntime, *, default_timeout_sec: int):
        self.runtime = runtime
        self.default_timeout_sec = default_timeout_sec

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timeout_sec": {"type": "integer"},
                "max_events": {"type": "integer"},
            },
            "required": [],
        }

    async def execute(self, timeout_sec: int | None = None, max_events: int = 20) -> str:
        try:
            ctx, err = _ctx()
            if err:
                return err
            if ctx.role != "lead":
                return "Error: TeamWait is lead-only"
            events = await self.runtime.wait_events(
                timeout_sec=timeout_sec or self.default_timeout_sec,
                max_events=max_events,
            )
            return _json(events)
        except Exception as exc:
            return f"Error: {exc}"


class TeamShutdownTool(BaseTool):
    """Shutdown one teammate or cleanup team."""

    name = "TeamShutdown"
    description = "Shutdown one teammate or clean up entire team."

    def __init__(self, runtime: TeamRuntime):
        self.runtime = runtime

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "description": "Optional teammate id"},
            },
            "required": [],
        }

    async def execute(self, member_id: str = "") -> str:
        try:
            ctx, err = _ctx()
            if err:
                return err
            if ctx.role != "lead":
                return "Error: TeamShutdown is lead-only"
            if member_id:
                await self.runtime.shutdown_member(actor_role="lead", member_id=member_id)
                return f"Shutdown request completed for {member_id}"
            await self.runtime.cleanup_team(actor_role="lead")
            return "Team cleaned up."
        except Exception as exc:
            return f"Error: {exc}"
