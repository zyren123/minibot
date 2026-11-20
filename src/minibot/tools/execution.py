"""Shared tool dispatch with text-compatible hooks."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..hooks.manager import HookManager
from .permissions import current_permissions
from .registry import ToolRegistry
from .result import ToolOutcome


async def execute_with_hooks(
    registry: ToolRegistry, hooks: HookManager, name: str, args: dict[str, Any],
    *, actor: str = "agent",
) -> ToolOutcome:
    permissions = current_permissions.get()
    call = None
    if permissions is not None and permissions.policy.mode != "off":
        tool = registry.get(name)
        if tool is None:
            return ToolOutcome(f"Unknown tool: {name}", "unknown_tool")
        call = await permissions.authorize(name, args, tool, actor)
        if call is None:
            return ToolOutcome("Tool execution refused by permissions.", "refused")
        args = call.args

    try:
        blocked, reason = await hooks.trigger_pre_tool_call(name, deepcopy(args) if call is not None else args)
    except Exception as exc:
        return ToolOutcome(
            f"Error: Pre-tool hook failed: {exc}", "error",
            hook_error=str(exc), hook_phase="pre",
        )
    if blocked:
        return ToolOutcome(f"Blocked by hook: {reason}", "refused")

    if call is not None and (
        (call.requires_active_run and not permissions.active)
        or registry.get(name) is not tool or not call.still_targets(tool)
    ):
        return ToolOutcome("Approved tool or target changed before execution.", "refused")
    outcome = await registry.execute_result(name, args)
    try:
        await hooks.trigger_post_tool_call(name, args, outcome.text)
    except Exception as exc:
        return replace(outcome, hook_error=str(exc), hook_phase="post")
    return outcome
