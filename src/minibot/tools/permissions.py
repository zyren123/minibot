"""Execution-layer policy and approval for individual tool invocations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from ..config.schema import ToolPermissionsConfig
from .base import BaseTool
from .builtin.file import EditFileTool, ListDirTool, ReadFileTool, WorkspaceFileTool, WriteFileTool
from .builtin.workspace_backend import WorkspacePath

ApprovalSource = Literal["policy", "human", "auto_review"]
ReviewDecision = Literal["approve", "deny", "ask"]
_FILE_TYPES = (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool)
_WRITE_TYPES = (WriteFileTool, EditFileTool)
_MAX_REVIEW_INPUT = 4_000


@dataclass(frozen=True)
class ToolApprovalRequest:
    approval_id: str
    tool_name: str
    actor: str
    preview: str


ToolApprovalHandler = Callable[[ToolApprovalRequest], Awaitable[bool]]
ToolReviewer = Callable[[str], Awaitable[ReviewDecision]]
ApprovalObserver = Callable[[ToolApprovalRequest, bool, ApprovalSource], Awaitable[None]]


def parse_review(text: str) -> ReviewDecision:
    """Require a single decision with no additional instructions."""
    try:
        result = json.loads(text, object_pairs_hook=list)
    except (ValueError, RecursionError):
        return "ask"
    if result == [("decision", "approve")]:
        return "approve"
    if result == [("decision", "deny")]:
        return "deny"
    return "ask"


@dataclass(frozen=True)
class ApprovedCall:
    args: dict[str, Any]
    file: WorkspacePath | None = None
    requires_active_run: bool = False

    def still_targets(self, tool: BaseTool) -> bool:
        return self.file is None or _resolve_target(tool, self.args) == self.file


def _resolve_target(tool: BaseTool, args: dict[str, Any]) -> WorkspacePath | None:
    path = args.get("path")
    if not isinstance(tool, WorkspaceFileTool) or not isinstance(path, str) or not path:
        return None
    try:
        return tool.permission_target(path)
    except (OSError, RuntimeError, ValueError):
        return None


@dataclass
class PermissionRun:
    policy: ToolPermissionsConfig
    human: ToolApprovalHandler | None
    review: ToolReviewer
    observe: ApprovalObserver
    user_intent: str | None
    active: bool = True

    async def authorize(
        self, name: str, args: dict[str, Any], tool: BaseTool, actor: str,
    ) -> ApprovedCall | None:
        choice = self.policy.rules.get(name, self.policy.default)
        target = None
        if type(tool) in _FILE_TYPES:
            target = _resolve_target(tool, args)
            if target is None:
                choice = "deny"
        # Model tool arguments are JSON. This snapshot is independent of the input
        # and of any mutable copy passed to hooks later.
        try:
            encoded = json.dumps(args, ensure_ascii=True, allow_nan=False, indent=2)
            arguments = json.loads(encoded)
        except (TypeError, ValueError, RecursionError):
            return None

        if target is not None:
            arguments["path"] = target.relative
            encoded = json.dumps(arguments, ensure_ascii=True, allow_nan=False, indent=2)

        call = ApprovedCall(arguments, target, requires_active_run=choice == "ask")
        if choice == "allow":
            return call
        if not self.active:
            return None
        request = ToolApprovalRequest(uuid4().hex, name, actor, encoded)
        if choice == "deny":
            await self.observe(request, False, "policy")
            return None

        # The reviewer cannot grant permission outside an explicitly configured
        # path, or approve generic tools that may have external effects.
        if self._can_review(tool, call, encoded):
            brief = json.dumps(
                {"user_request": self.user_intent, "tool": name, "arguments": arguments},
                ensure_ascii=True, allow_nan=False,
            )
            if len(brief) <= _MAX_REVIEW_INPUT:
                decision = await self.review(brief)
                if decision != "ask":
                    approved = decision == "approve"
                    if self.active:
                        await self.observe(request, approved, "auto_review")
                    return call if approved and self.active else None

        approved = False
        if self.human is not None and self.active:
            try:
                async with asyncio.timeout(self.policy.timeout):
                    approved = (await self.human(request)) is True
            except TimeoutError:
                pass
        if self.active:
            await self.observe(request, approved, "human")
        return call if approved and self.active else None

    def _can_review(self, tool: BaseTool, call: ApprovedCall, preview: str) -> bool:
        if (
            self.policy.mode != "auto_review" or not self.user_intent or type(tool) not in _WRITE_TYPES
            or call.file is None or len(preview) > _MAX_REVIEW_INPUT
        ):
            return False
        target = PurePosixPath(call.args["path"])
        if any(part.startswith(".") for part in target.parts):
            return False
        return any(fnmatchcase(target.as_posix(), pattern) for pattern in self.policy.auto_review_paths)


current_permissions: ContextVar[PermissionRun | None] = ContextVar("tool_permissions", default=None)
