"""Team orchestration runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from ..config.schema import TeamsConfig
from ..hooks.manager import HookManager
from .models import MemberStatus, TeamMember, TeamTask, as_dict
from .store import TeamStore


class TeamRuntime:
    """Manages one active in-process team for the current lead session."""

    def __init__(
        self,
        *,
        config: TeamsConfig,
        workdir: Path,
        hook_manager: HookManager | None = None,
    ):
        self.config = config
        self.workdir = workdir
        self.hook_manager = hook_manager
        self.store = TeamStore((workdir / config.log_dir).resolve())

        self._agent_factory: Callable[[str, str], Any] | None = None
        self._member_workers: dict[str, asyncio.Task[None]] = {}
        self._member_agents: dict[str, Any] = {}
        self._member_histories: dict[str, list[dict[str, Any]]] = {}

    def set_agent_factory(self, factory: Callable[[str, str], Any]) -> None:
        """Inject teammate agent factory."""
        self._agent_factory = factory

    async def create_team(
        self,
        *,
        actor_role: str,
        team_name: str,
        member_count: int,
    ) -> dict[str, Any]:
        """Create team and spawn teammate workers."""
        if actor_role not in {"lead", "solo"}:
            raise PermissionError("Only lead can create a team")
        if member_count < 1:
            raise ValueError("member_count must be >= 1")
        if member_count > self.config.max_members:
            raise ValueError(f"member_count exceeds max_members={self.config.max_members}")
        if self._agent_factory is None:
            raise RuntimeError("Team runtime is missing teammate agent factory")

        teammate_ids = [f"member-{i}" for i in range(1, member_count + 1)]
        team = await self.store.create_team(
            team_name=team_name.strip() or "default-team",
            lead_id="lead",
            teammate_ids=teammate_ids,
        )
        await self.spawn_members(teammate_ids)
        return as_dict(team)

    async def spawn_members(self, teammate_ids: list[str]) -> None:
        """Spawn worker loops for teammates."""
        for member_id in teammate_ids:
            if member_id in self._member_workers:
                continue
            mailbox = await self.store.register_mailbox(member_id)
            team = self.store.team
            if team is None:
                raise RuntimeError("No active team")

            agent = self._agent_factory(team.team_id, member_id) if self._agent_factory else None
            if agent is None:
                raise RuntimeError("Failed to create teammate agent")

            self._member_agents[member_id] = agent
            self._member_histories[member_id] = []
            self._member_workers[member_id] = asyncio.create_task(
                self._member_worker(member_id, mailbox)
            )

    async def _member_worker(
        self,
        member_id: str,
        mailbox: "asyncio.Queue[dict[str, Any]]",
    ) -> None:
        """Worker loop for one teammate."""
        while True:
            item = await mailbox.get()
            kind = item.get("kind", "message")
            if kind == "shutdown":
                await self.store.set_member_status(member_id, MemberStatus.STOPPED)
                await self.store.release_member_locks(member_id)
                await self.store.emit_event(
                    {
                        "type": "member_stopped",
                        "member_id": member_id,
                    }
                )
                return

            prompt = self._build_member_prompt(item)
            await self.store.set_member_status(member_id, MemberStatus.IN_PROGRESS)
            await self.store.emit_event(
                {
                    "type": "member_started",
                    "member_id": member_id,
                    "trigger": kind,
                }
            )

            history = self._member_histories.setdefault(member_id, [])
            history.append({"role": "user", "content": prompt})
            agent = self._member_agents[member_id]

            try:
                await agent.run_loop(history)
                await self.store.set_member_status(member_id, MemberStatus.IDLE)
                await self.store.release_member_locks(member_id)
                payload = {
                    "type": "teammate_idle",
                    "member_id": member_id,
                    "trigger": kind,
                }
                await self.store.emit_event(payload)
                if self.hook_manager is not None:
                    await self.hook_manager.trigger_teammate_idle(
                        member_id=member_id,
                        detail=json.dumps(payload),
                    )
            except Exception as exc:
                await self.store.set_member_status(
                    member_id,
                    MemberStatus.ERROR,
                    last_error=str(exc),
                )
                await self.store.release_member_locks(member_id)
                await self.store.emit_event(
                    {
                        "type": "member_error",
                        "member_id": member_id,
                        "error": str(exc),
                    }
                )

    def _build_member_prompt(self, item: dict[str, Any]) -> str:
        kind = item.get("kind")
        if kind == "task_assigned":
            task = item["task"]
            return (
                "You received an assigned team task.\n"
                f"Task ID: {task['task_id']}\n"
                f"Title: {task['title']}\n"
                f"Details: {task['details']}\n"
                "Complete it autonomously and coordinate via TeamMessage/TeamBroadcast."
            )
        sender = item.get("sender_id", "unknown")
        content = item.get("content", "")
        return (
            f"You received a message from {sender}.\n"
            f"Message: {content}\n"
            "If action is required, do it and report back via team messaging."
        )

    def _ensure_team(self) -> None:
        if not self.store.has_team:
            raise ValueError("No active team")

    async def list_members(self) -> list[dict[str, Any]]:
        """List current members."""
        members = await self.store.list_members()
        return [as_dict(m) for m in members]

    async def send_message(
        self,
        *,
        sender_id: str,
        recipient_id: str,
        content: str,
    ) -> dict[str, Any]:
        """Direct message from one member to another."""
        self._ensure_team()
        msg = await self.store.add_message(
            sender_id=sender_id,
            recipient_id=recipient_id,
            content=content,
            kind="direct",
        )
        if recipient_id != "lead":
            await self.store.enqueue_mail(
                recipient_id,
                {
                    "kind": "message",
                    "sender_id": sender_id,
                    "content": content,
                },
            )
        return as_dict(msg)

    async def broadcast(
        self,
        *,
        sender_id: str,
        content: str,
    ) -> dict[str, Any]:
        """Broadcast message to all members except sender."""
        self._ensure_team()
        members = await self.store.list_members()
        delivered = 0
        for member in members:
            if member.member_id == sender_id:
                continue
            await self.store.add_message(
                sender_id=sender_id,
                recipient_id=member.member_id,
                content=content,
                kind="broadcast",
            )
            if member.member_id != "lead":
                await self.store.enqueue_mail(
                    member.member_id,
                    {
                        "kind": "message",
                        "sender_id": sender_id,
                        "content": content,
                    },
                )
            delivered += 1
        return {"delivered": delivered}

    async def create_task(
        self,
        *,
        actor_role: str,
        actor_id: str,
        title: str,
        details: str,
        assignee_id: str | None = None,
    ) -> dict[str, Any]:
        """Create task (lead only)."""
        if actor_role != "lead":
            raise PermissionError("Only lead can create tasks")
        task = await self.store.create_task(
            title=title,
            details=details,
            created_by=actor_id,
            assignee_id=assignee_id,
        )
        if assignee_id not in (None, "lead"):
            await self.store.enqueue_mail(
                assignee_id,
                {"kind": "task_assigned", "task": as_dict(task)},
            )
        return as_dict(task)

    async def assign_task(
        self,
        *,
        actor_role: str,
        task_id: str,
        assignee_id: str,
    ) -> dict[str, Any]:
        """Assign task to teammate (lead only)."""
        if actor_role != "lead":
            raise PermissionError("Only lead can assign tasks")
        task = await self.store.assign_task(task_id, assignee_id)
        if assignee_id != "lead":
            await self.store.enqueue_mail(
                assignee_id,
                {"kind": "task_assigned", "task": as_dict(task)},
            )
        return as_dict(task)

    async def claim_task(
        self,
        *,
        actor_role: str,
        actor_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Claim task (teammates only)."""
        if actor_role != "teammate":
            raise PermissionError("Only teammates can claim tasks")
        task = await self.store.claim_task(actor_id, task_id=task_id)
        return as_dict(task) if task else {}

    async def complete_task(
        self,
        *,
        actor_role: str,
        actor_id: str,
        task_id: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Complete task (lead or owning teammate)."""
        tasks = await self.store.list_tasks()
        task = next((t for t in tasks if t.task_id == task_id), None)
        if task is None:
            raise ValueError(f"Unknown task: {task_id}")
        if actor_role == "teammate" and task.assignee_id != actor_id:
            raise PermissionError("Teammate can only complete own task")

        completed = await self.store.complete_task(
            task_id=task_id,
            completed_by=actor_id,
            note=note,
        )
        await self.store.release_member_locks(actor_id)
        if self.hook_manager is not None:
            await self.hook_manager.trigger_task_completed(
                task_id=task_id,
                detail=note or "",
            )
        return as_dict(completed)

    async def list_tasks(self) -> list[dict[str, Any]]:
        """List all tasks."""
        tasks = await self.store.list_tasks()
        return [as_dict(t) for t in tasks]

    async def wait_events(self, timeout_sec: int, max_events: int = 20) -> list[dict[str, Any]]:
        """Wait for team runtime events."""
        self._ensure_team()
        return await self.store.wait_events(timeout_sec, max_events=max_events)

    async def drain_inbox(self, member_id: str) -> list[dict[str, Any]]:
        """Read pending inbox items without blocking."""
        self._ensure_team()
        mailbox = await self.store.register_mailbox(member_id)
        drained: list[dict[str, Any]] = []
        while True:
            try:
                drained.append(mailbox.get_nowait())
            except asyncio.QueueEmpty:
                break
        return drained

    async def shutdown_member(self, *, actor_role: str, member_id: str) -> None:
        """Gracefully stop one teammate."""
        if actor_role != "lead":
            raise PermissionError("Only lead can shut down teammates")
        if member_id == "lead":
            raise ValueError("Cannot shutdown lead member")
        worker = self._member_workers.get(member_id)
        agent = self._member_agents.get(member_id)
        if worker is None:
            self._member_agents.pop(member_id, None)
            self._member_histories.pop(member_id, None)
            await self.store.release_member_locks(member_id)
            return

        timeout = max(5, self.config.wait_timeout_sec)
        try:
            await self.store.enqueue_mail(member_id, {"kind": "shutdown"})
            await asyncio.wait_for(worker, timeout=timeout)
        except asyncio.TimeoutError:
            worker.cancel()
            try:
                await asyncio.wait_for(worker, timeout=1)
            except (asyncio.TimeoutError, BaseException):
                pass
            await self.store.set_member_status(member_id, MemberStatus.ERROR, last_error="shutdown timeout")
            await self.store.emit_event(
                {
                    "type": "member_error",
                    "member_id": member_id,
                    "error": "shutdown timeout",
                }
            )
        finally:
            try:
                await self.store.release_member_locks(member_id)
            except Exception:
                pass
            if agent is not None and hasattr(agent, "close_client"):
                try:
                    close_result = agent.close_client()
                    if inspect.isawaitable(close_result):
                        await close_result
                except Exception:
                    pass
            self._member_workers.pop(member_id, None)
            self._member_agents.pop(member_id, None)
            self._member_histories.pop(member_id, None)

    async def cleanup_team(self, *, actor_role: str) -> None:
        """Shutdown all workers and clear team state."""
        if actor_role != "lead":
            raise PermissionError("Only lead can clean up team")
        for member_id in list(self._member_workers.keys()):
            try:
                await self.shutdown_member(actor_role="lead", member_id=member_id)
            except Exception:
                pass
        self._member_workers.clear()
        self._member_agents.clear()
        self._member_histories.clear()
        await self.store.emit_event({"type": "team_cleaned_up"})
        await self.store.clear_team()

    async def acquire_file_lock(self, *, member_id: str, path: str) -> tuple[bool, str | None]:
        """Acquire file lock for teammate write."""
        return await self.store.acquire_file_lock(path=path, member_id=member_id)

    async def acquire_workspace_lock(self, *, member_id: str) -> tuple[bool, str | None]:
        """Acquire workspace lock for teammate bash."""
        return await self.store.acquire_workspace_lock(member_id=member_id)

    async def release_member_locks(self, member_id: str) -> None:
        """Release all locks held by one member."""
        await self.store.release_member_locks(member_id)

    async def get_summary(self) -> dict[str, Any]:
        """Current team summary."""
        return await self.store.summary()

    async def enqueue_teammate_task(self, member_id: str, prompt: str) -> None:
        """Force-send one custom task prompt to teammate."""
        await self.store.enqueue_mail(
            member_id,
            {
                "kind": "message",
                "sender_id": "lead",
                "content": prompt,
            },
        )
