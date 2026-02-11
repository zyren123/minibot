"""Hook manager for registering and triggering hooks."""

from pathlib import Path
from typing import Any

from ..config.schema import HooksConfig, HookDefinition
from .events import HookEvent, HookContext, HookResult
from .executor import HookExecutor


class HookManager:
    """Manages hook registration and execution."""

    def __init__(self, config: HooksConfig, workdir: Path):
        self.config = config
        self.workdir = workdir
        self.enabled = config.enabled
        self.executor = HookExecutor(workdir)

        # Organize hooks by event
        self._hooks: dict[HookEvent, list[HookDefinition]] = {}
        for hook in config.hooks:
            if hook.enabled:
                event = HookEvent(hook.event)
                if event not in self._hooks:
                    self._hooks[event] = []
                self._hooks[event].append(hook)

    def get_hooks(self, event: HookEvent) -> list[HookDefinition]:
        """Get all hooks for an event."""
        return self._hooks.get(event, [])

    async def trigger(
        self,
        event: HookEvent,
        context: HookContext,
    ) -> list[HookResult]:
        """Trigger all hooks for an event."""
        if not self.enabled:
            return []

        hooks = self.get_hooks(event)
        if not hooks:
            return []

        results = []
        for hook in hooks:
            result = await self.executor.execute(
                handler=hook.handler,
                context=context,
                timeout=hook.timeout,
            )
            results.append(result)

            # If any hook blocks, stop processing
            if result.blocked:
                break

        return results

    async def trigger_pre_tool_call(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Trigger pre-tool-call hooks. Returns (blocked, reason)."""
        from .events import ToolCallContext

        context = ToolCallContext(
            event=HookEvent.PRE_TOOL_CALL,
            workdir=str(self.workdir),
            tool_name=tool_name,
            tool_args=tool_args,
        )

        results = await self.trigger(HookEvent.PRE_TOOL_CALL, context)

        for result in results:
            if result.blocked:
                return True, result.block_reason

        return False, None

    async def trigger_post_tool_call(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: str,
    ) -> None:
        """Trigger post-tool-call hooks."""
        from .events import ToolCallContext

        context = ToolCallContext(
            event=HookEvent.POST_TOOL_CALL,
            workdir=str(self.workdir),
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
        )

        await self.trigger(HookEvent.POST_TOOL_CALL, context)

    async def trigger_session_start(self, session_id: str = "") -> list[HookResult]:
        """Trigger session start hooks."""
        from .events import SessionContext

        context = SessionContext(
            event=HookEvent.SESSION_START,
            workdir=str(self.workdir),
            session_id=session_id,
        )

        return await self.trigger(HookEvent.SESSION_START, context)

    async def trigger_session_end(self, session_id: str = "") -> list[HookResult]:
        """Trigger session end hooks."""
        from .events import SessionContext

        context = SessionContext(
            event=HookEvent.SESSION_END,
            workdir=str(self.workdir),
            session_id=session_id,
        )

        return await self.trigger(HookEvent.SESSION_END, context)

    async def trigger_teammate_idle(
        self,
        *,
        member_id: str,
        detail: str = "",
    ) -> list[HookResult]:
        """Trigger teammate idle hooks."""
        from .events import TeamEventContext

        context = TeamEventContext(
            event=HookEvent.TEAMMATE_IDLE,
            workdir=str(self.workdir),
            team_member_id=member_id,
            detail=detail,
        )
        return await self.trigger(HookEvent.TEAMMATE_IDLE, context)

    async def trigger_task_completed(
        self,
        *,
        task_id: str,
        detail: str = "",
    ) -> list[HookResult]:
        """Trigger task completed hooks."""
        from .events import TeamEventContext

        context = TeamEventContext(
            event=HookEvent.TASK_COMPLETED,
            workdir=str(self.workdir),
            task_id=task_id,
            detail=detail,
        )
        return await self.trigger(HookEvent.TASK_COMPLETED, context)
