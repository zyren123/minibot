"""Shared tool dispatch with text-compatible hooks."""

from dataclasses import replace
from typing import Any

from ..hooks.manager import HookManager
from .registry import ToolRegistry
from .result import ToolOutcome


async def execute_with_hooks(
    registry: ToolRegistry, hooks: HookManager, name: str, args: dict[str, Any],
) -> ToolOutcome:
    try:
        blocked, reason = await hooks.trigger_pre_tool_call(name, args)
    except Exception as exc:
        return ToolOutcome(
            f"Error: Pre-tool hook failed: {exc}", "error",
            hook_error=str(exc), hook_phase="pre",
        )
    if blocked:
        return ToolOutcome(f"Blocked by hook: {reason}", "refused")

    outcome = await registry.execute_result(name, args)
    try:
        await hooks.trigger_post_tool_call(name, args, outcome.text)
    except Exception as exc:
        return replace(outcome, hook_error=str(exc), hook_phase="post")
    return outcome
