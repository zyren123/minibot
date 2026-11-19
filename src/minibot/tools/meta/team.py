"""Team orchestration tools."""

from __future__ import annotations

import json
from typing import Any

from ..base import ResultTool
from ..result import ToolOutcome
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


class TeamCreateTool(ResultTool):
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

    async def execute_result(
        self,
        team_name: str = "default-team",
        member_count: int | None = None,
    ) -> ToolOutcome:
        try:
            ctx, err = _ctx()
            if err:
                return ToolOutcome(err, "error")
            info = await self.runtime.create_team(
                actor_role=ctx.role,
                team_name=team_name,
                member_count=member_count if member_count is not None else self.default_members,
            )
            return ToolOutcome(_json(info), "success")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class TeamMembersTool(ResultTool):
    """List team members and statuses."""

    name = "TeamMembers"
    description = "List active team members and statuses."

    def __init__(self, runtime: TeamRuntime):
        self.runtime = runtime

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute_result(self) -> ToolOutcome:
        try:
            members = await self.runtime.list_members()
            return ToolOutcome(_json(members), "success")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class TeamTaskTool(ResultTool):
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

    async def execute_result(
        self,
        action: str,
        title: str = "",
        details: str = "",
        task_id: str = "",
        assignee_id: str = "",
        note: str = "",
    ) -> ToolOutcome:
        try:
            ctx, err = _ctx()
            if err:
                return ToolOutcome(err, "error")
            actor_role = ctx.role
            actor, err = _actor_id(ctx)
            if err:
                return ToolOutcome(err, "error")

            if action == "list":
                return ToolOutcome(_json(await self.runtime.list_tasks()), "success")

            if action == "create":
                task = await self.runtime.create_task(
                    actor_role=actor_role,
                    actor_id=actor,
                    title=title,
                    details=details,
                    assignee_id=assignee_id or None,
                )
                return ToolOutcome(_json(task), "success")

            if action == "assign":
                task = await self.runtime.assign_task(
                    actor_role=actor_role,
                    task_id=task_id,
                    assignee_id=assignee_id,
                )
                return ToolOutcome(_json(task), "success")

            if action == "claim":
                task = await self.runtime.claim_task(
                    actor_role=actor_role,
                    actor_id=actor,
                    task_id=task_id or None,
                )
                return ToolOutcome(_json(task), "success")

            if action == "complete":
                task = await self.runtime.complete_task(
                    actor_role=actor_role,
                    actor_id=actor,
                    task_id=task_id,
                    note=note or None,
                )
                return ToolOutcome(_json(task), "success")

            return ToolOutcome(f"Error: Unknown action '{action}'", "error")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class TeamMessageTool(ResultTool):
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

    async def execute_result(self, to_member_id: str, content: str) -> ToolOutcome:
        try:
            ctx, err = _ctx()
            if err:
                return ToolOutcome(err, "error")
            sender_id, err = _actor_id(ctx)
            if err:
                return ToolOutcome(err, "error")
            msg = await self.runtime.send_message(
                sender_id=sender_id,
                recipient_id=to_member_id,
                content=content,
            )
            return ToolOutcome(_json(msg), "success")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class TeamBroadcastTool(ResultTool):
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

    async def execute_result(self, content: str) -> ToolOutcome:
        try:
            ctx, err = _ctx()
            if err:
                return ToolOutcome(err, "error")
            sender_id, err = _actor_id(ctx)
            if err:
                return ToolOutcome(err, "error")
            result = await self.runtime.broadcast(
                sender_id=sender_id,
                content=content,
            )
            return ToolOutcome(_json(result), "success")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class TeamWaitTool(ResultTool):
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

    async def execute_result(self, timeout_sec: int | None = None, max_events: int = 20) -> ToolOutcome:
        try:
            ctx, err = _ctx()
            if err:
                return ToolOutcome(err, "error")
            if ctx.role != "lead":
                return ToolOutcome("Error: TeamWait is lead-only", "refused")
            events = await self.runtime.wait_events(
                timeout_sec=timeout_sec or self.default_timeout_sec,
                max_events=max_events,
            )
            return ToolOutcome(_json(events), "success")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")


class TeamShutdownTool(ResultTool):
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

    async def execute_result(self, member_id: str = "") -> ToolOutcome:
        try:
            ctx, err = _ctx()
            if err:
                return ToolOutcome(err, "error")
            if ctx.role != "lead":
                return ToolOutcome("Error: TeamShutdown is lead-only", "refused")
            if member_id:
                await self.runtime.shutdown_member(actor_role="lead", member_id=member_id)
                return ToolOutcome(f"Shutdown request completed for {member_id}", "success")
            await self.runtime.cleanup_team(actor_role="lead")
            return ToolOutcome("Team cleaned up.", "success")
        except Exception as exc:
            return ToolOutcome(f"Error: {exc}", "error")
