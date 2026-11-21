"""Shared tool dispatch with text-compatible hooks."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..core.artifacts import ArtifactError, current_artifacts
from ..hooks.manager import HookManager
from .builtin.artifact import ReadArtifactTool, SearchArtifactTool
from .builtin.ask_user import AskUserQuestionTool
from .permissions import ApprovedCall, current_permissions
from .registry import ToolRegistry
from .result import ToolOutcome


async def execute_with_hooks(
    registry: ToolRegistry, hooks: HookManager | None, name: str, args: dict[str, Any],
    *, actor: str = "agent",
    before_effects: Callable[[], None] | None = None,
    wait_for_approval: Callable[[Callable[[], Awaitable[ApprovedCall | None]]], Awaitable[ApprovedCall | None]] | None = None,
    invoke: Callable[[dict[str, Any]], Awaitable[ToolOutcome]] | None = None,
) -> ToolOutcome:
    permissions = current_permissions.get()
    call = None
    tool = registry.get(name)
    if permissions is not None and permissions.policy.mode != "off":
        if tool is None:
            return ToolOutcome(f"Unknown tool: {name}", "unknown_tool")
        async def authorize() -> ApprovedCall | None:
            return await permissions.authorize(name, args, tool, actor)

        choice = permissions.policy.rules.get(name, permissions.policy.default)
        call = await wait_for_approval(authorize) if choice == "ask" and wait_for_approval else await authorize()
        if call is None:
            return ToolOutcome("Tool execution refused by permissions.", "refused")
        args = call.args

    if before_effects is not None:
        before_effects()
    if hooks is not None:
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
    outcome = await registry.execute_result(name, args) if invoke is None else await invoke(args)
    if hooks is not None:
        try:
            await hooks.trigger_post_tool_call(name, args, outcome.text)
        except Exception as exc:
            outcome = replace(outcome, hook_error=str(exc), hook_phase="post")

    scope = current_artifacts.get()
    if scope is None or not outcome.artifact_eligible or isinstance(
        tool, (ReadArtifactTool, SearchArtifactTool, AskUserQuestionTool),
    ):
        return outcome
    if len(outcome.text) <= scope.threshold_chars:
        return outcome
    try:
        artifact = await asyncio.to_thread(scope.store.save, outcome.text, scope.run_id, name)
    except ArtifactError as exc:
        return replace(outcome, text=scope.failure(str(exc)), status="error", artifact_eligible=False)
    scope.record("artifact_created", artifact)
    reference = f"Artifact {artifact.artifact_id}"
    size = f" ({artifact.size_bytes} bytes)"
    if scope.output_chars >= len(reference) + len(size):
        reference += size
    if scope.output_chars >= len(reference) + 4:
        reference += "\n" + outcome.text[:min(512, scope.output_chars - len(reference) - 1)]
    return replace(outcome, text=reference, artifact_id=artifact.artifact_id)
