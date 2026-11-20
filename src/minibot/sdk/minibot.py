"""Programmatic SDK wrapper for Minibot."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
import uuid
from typing import Any, AsyncIterator, Callable, Iterable, Iterator

from ..agent import Agent, UserInterruptedError
from ..config import Config, load_config
from ..config.workspace import validate_workspace_runtime
from ..core.model_context import ContextWindowExceeded
from ..core.run_store import DurableRunError, DurableRunPaused, RunState, RunStore, StepState
from ..core.runtime_budget import RuntimeBudgetExceeded
from ..events import StreamEvent
from ..file_checkpoints import FileCheckpoint, FileCheckpointError, GitFileCheckpoints, finish_file_operation
from ..session.manager import SessionManager
from ..session.messages import normalize_tool_calls
from ..tools.base import BaseTool
from ..tools.permissions import ToolApprovalHandler, ToolApprovalRequest

from ._active_streams import own_stream
from ._event_router import RouterEventSink
from .types import ChatResult


ToolInput = BaseTool | Callable[..., Any] | dict[str, Any]


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return ""


def _last_assistant_usage(messages: list[dict[str, Any]]) -> dict[str, int] | None:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        usage = msg.get("usage")
        if isinstance(usage, dict) and usage:
            return dict(usage)
    return None


def _last_user_message_id(messages: list[dict[str, Any]]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        message_id = msg.get("message_id")
        if isinstance(message_id, str) and message_id.strip():
            return message_id
    return None


def _new_message_id() -> str:
    return f"msg-{uuid.uuid4().hex[:12]}"


@dataclass
class _StreamPersistenceState:
    session_id: str
    stop_on_ask_user_question: bool = False
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    persisted_message_ids: set[str] = field(default_factory=set)
    assistant_contents: dict[str, str] = field(default_factory=dict)
    assistant_reasoning: dict[str, str] = field(default_factory=dict)
    assistant_parent_ids: dict[str, str | None] = field(default_factory=dict)
    question_message_ids: dict[str, str] = field(default_factory=dict)
    producer: asyncio.Task[None] | None = None
    approvals_cancelled: bool = False


class Minibot:
    """A friendly wrapper around :class:`minibot.agent.Agent` for SDK usage."""

    def __init__(
        self,
        *,
        config: Config | None = None,
        workdir: str | Path | None = None,
        session_id: str | None = None,
        sessions_dir: str | Path | None = None,
        skills_dir: str | Path | Iterable[str | Path] | None = None,
        tools: list[ToolInput] | None = None,
        system_prompt: str | None = None,
        disabled_skills: Iterable[str] | None = None,
        allow_subagent_delegation: bool = True,
        allow_team_tools: bool = True,
        tool_approval_handler: ToolApprovalHandler | None = None,
        durable_execution: bool | None = None,
    ) -> None:
        if config is None:
            resolved_workdir = Path(workdir).resolve() if workdir is not None else None
            config = load_config(workdir=resolved_workdir)

        if durable_execution is not None:
            config = replace(config, durable_execution=durable_execution)

        if sessions_dir is not None:
            config.session.sessions_dir = str(Path(sessions_dir).expanduser().resolve())

        validate_workspace_runtime(config)
        if tools and config.workspace.restricted:
            raise ValueError("Custom Python tools are unavailable in a restricted Docker workspace")
        self.config = config
        self._event_router = RouterEventSink()
        self._session_manager = (
            SessionManager(Path(self.config.session.sessions_dir))
            if self.config.session.enabled
            else None
        )

        self.session_id = self._resolve_session_id(session_id)
        self.messages: list[dict[str, Any]] = (
            self._session_manager.load(self.session_id)
            if self._session_manager is not None
            else []
        )

        self._active_interrupt_queue: "asyncio.Queue[None] | None" = None
        self._pending_question: dict[str, Any] | None = None
        self._pending_question_future: "asyncio.Future[dict[str, Any]] | None" = None
        self._active_stream: _StreamPersistenceState | None = None
        self._active_durable_segment = False
        self._tool_approval_handler = tool_approval_handler
        self._pending_tool_approvals: dict[str, asyncio.Future[bool]] = {}

        self.agent = Agent(
            config=self.config,
            session_id=self.session_id,
            extra_system_prompt=system_prompt,
            event_sink=self._event_router,
            skills_dir=skills_dir,
            disabled_skills=disabled_skills,
            allow_subagent_delegation=allow_subagent_delegation,
            allow_team_tools=allow_team_tools,
            ask_user_handler=self._wait_for_user_answer,
            tool_approval_handler=self._wait_for_tool_approval,
        )

        if tools:
            for tool in tools:
                self.register_tool(tool)

    def _resolve_session_id(self, session_id: str | None) -> str:
        if session_id:
            if self._session_manager is not None and not self._session_manager.exists(session_id):
                self._session_manager.store.create(session_id)
            return session_id

        if self._session_manager is not None:
            return self._session_manager.create()

        return str(uuid.uuid4())[:8]

    def set_session(self, session_id: str) -> None:
        """Switch active session (and load its history if persistence is enabled)."""
        if self._active_stream is not None or self._active_durable_segment:
            raise RuntimeError("Cannot switch sessions while generation is running")
        self.session_id = self._resolve_session_id(session_id)
        self.agent.reset_session(self.session_id)
        self.messages = (
            self._session_manager.load(self.session_id)
            if self._session_manager is not None
            else []
        )

    def create_session(self) -> str:
        if self._active_stream is not None or self._active_durable_segment:
            raise RuntimeError("Cannot switch sessions while generation is running")
        if self._session_manager is None:
            session_id = str(uuid.uuid4())[:8]
        else:
            session_id = self._session_manager.create()
        self.set_session(session_id)
        return session_id

    @staticmethod
    def _event_message_id(event: StreamEvent) -> str | None:
        value = event.get("message_id")
        if isinstance(value, str) and value.strip():
            return value
        return None

    @staticmethod
    def _event_parent_user_message_id(event: StreamEvent) -> str | None:
        value = event.get("parent_user_message_id")
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _persisted_ids_for_active_run(self) -> set[str]:
        return self._active_stream.persisted_message_ids if self._active_stream else set()

    def _save_streaming_runtime_state(
        self, state: _StreamPersistenceState, *, message_id: str, parent_user_message_id: str | None,
    ) -> None:
        if self._session_manager is None:
            return
        payload: dict[str, Any] = {
            "version": 1,
            "state": "streaming",
            "run_id": state.run_id,
            "assistant_message_id": message_id,
        }
        if parent_user_message_id:
            payload["parent_user_message_id"] = parent_user_message_id
        self._session_manager.save_runtime_state(state.session_id, payload)

    def _persist_assistant_message(
        self,
        *,
        state: _StreamPersistenceState,
        message_id: str,
        parent_user_message_id: str | None,
        content: str,
        reasoning: str = "",
        usage: dict[str, int] | None = None,
        context_usage: dict[str, int] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        if self._session_manager is None:
            return

        state.assistant_parent_ids[message_id] = parent_user_message_id
        state.assistant_contents[message_id] = content
        state.assistant_reasoning[message_id] = reasoning

        def build_message(base: dict[str, Any] | None = None) -> dict[str, Any]:
            message: dict[str, Any] = {
                "role": "assistant",
                "message_id": message_id,
                "parent_user_message_id": parent_user_message_id,
                "content": content,
                "completion_state": "complete",
            }
            if base is not None:
                message.update(base)
                message["role"] = "assistant"
                message["message_id"] = message_id
                message["parent_user_message_id"] = parent_user_message_id
                message["content"] = content
                message["completion_state"] = "complete"
            if reasoning:
                message["reasoning"] = reasoning
            elif base is not None and "reasoning" in message:
                message.pop("reasoning", None)
            if usage:
                message["usage"] = dict(usage)
            if context_usage:
                message["context_usage"] = dict(context_usage)
            if tool_calls:
                normalized_tool_calls = normalize_tool_calls(tool_calls)
                if normalized_tool_calls:
                    message["tool_calls"] = normalized_tool_calls
            if extra_fields:
                message.update(extra_fields)
            return message

        if message_id in state.persisted_message_ids:
            self._session_manager.update_message(
                state.session_id,
                message_id,
                lambda current: build_message(dict(current)),
            )
            return

        self._session_manager.append_message(state.session_id, build_message())
        state.persisted_message_ids.add(message_id)

    def _persist_answer_message_for_question(
        self,
        *,
        question_id: str,
        state: _StreamPersistenceState,
    ) -> None:
        if self._session_manager is None:
            return

        persisted_ids = self._persisted_ids_for_active_run()
        for message in reversed(self.messages):
            if message.get("role") != "user":
                continue
            if message.get("answer_to_question_id") != question_id:
                continue
            message_id = message.get("message_id")
            if not isinstance(message_id, str) or not message_id.strip():
                continue
            if message_id in persisted_ids:
                return
            self._session_manager.append_message(state.session_id, dict(message))
            state.persisted_message_ids.add(message_id)
            return

    def _update_in_memory_message(
        self,
        message_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any] | None:
        for index, message in enumerate(self.messages):
            if message.get("message_id") != message_id:
                continue
            updated = updater(dict(message))
            self.messages[index] = updated
            return updated
        return None

    async def _handle_stream_persistence_event(
        self,
        event: StreamEvent,
        state: _StreamPersistenceState,
    ) -> None:
        if self._session_manager is None:
            return

        event_type = event.get("type")
        if self.agent.run_store is not None:
            if event_type == "ask_user_question":
                durable_run_id = (event.get("data") or {}).get("durable_run_id")
                self._session_manager.save_runtime_state(state.session_id, {
                    "version": 1,
                    "state": "awaiting_user_answer",
                    "durable_run_id": durable_run_id,
                    "assistant_message_id": event.get("message_id"),
                    "pending_question": {
                        key: event[key] for key in (
                            "message_id", "question_id", "prompt", "options", "allow_free_text", "required",
                        )
                    },
                })
            return
        message_id = self._event_message_id(event)
        parent_user_message_id = self._event_parent_user_message_id(event)
        if message_id is not None and message_id not in state.assistant_parent_ids:
            state.assistant_parent_ids[message_id] = parent_user_message_id

        if event_type == "assistant_start":
            if message_id is None:
                return
            self._persist_assistant_message(
                state=state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
                content=state.assistant_contents.get(message_id, ""),
                reasoning=state.assistant_reasoning.get(message_id, ""),
            )
            self._save_streaming_runtime_state(
                state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
            )
            return

        if event_type == "assistant_reasoning_delta":
            if message_id is None:
                return
            next_reasoning = state.assistant_reasoning.get(message_id, "") + str(event.get("reasoning_text") or "")
            self._persist_assistant_message(
                state=state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
                content=state.assistant_contents.get(message_id, ""),
                reasoning=next_reasoning,
            )
            self._save_streaming_runtime_state(
                state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
            )
            return

        if event_type == "assistant_delta":
            if message_id is None:
                return
            next_content = state.assistant_contents.get(message_id, "") + str(event.get("delta_text") or "")
            self._persist_assistant_message(
                state=state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
                content=next_content,
                reasoning=state.assistant_reasoning.get(message_id, ""),
            )
            self._save_streaming_runtime_state(
                state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
            )
            return

        if event_type == "assistant_end":
            if message_id is None:
                return
            content = str(event.get("content") or state.assistant_contents.get(message_id, ""))
            reasoning = str(event.get("reasoning") or state.assistant_reasoning.get(message_id, ""))
            usage = event.get("usage")
            tool_calls = event.get("tool_calls")
            self._persist_assistant_message(
                state=state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
                content=content,
                reasoning=reasoning,
                usage=usage if isinstance(usage, dict) and usage else None,
                tool_calls=tool_calls if isinstance(tool_calls, list) and tool_calls else None,
            )
            if event.get("finish_reason") != "tool_calls":
                self._clear_streaming_state(state)
            return

        if event_type == "ask_user_question":
            if message_id is None:
                return
            question_id = str(event.get("question_id") or "").strip()
            prompt = str(event.get("prompt") or "")
            options = [dict(item) for item in event.get("options") or []]
            allow_free_text = bool(event.get("allow_free_text", True))
            required = bool(event.get("required", True))
            usage = event.get("usage")
            context_usage = event.get("context_usage")
            state.question_message_ids[question_id] = message_id
            self._persist_assistant_message(
                state=state,
                message_id=message_id,
                parent_user_message_id=state.assistant_parent_ids.get(message_id),
                content=state.assistant_contents.get(message_id) or prompt,
                reasoning=state.assistant_reasoning.get(message_id, ""),
                usage=usage if isinstance(usage, dict) and usage else None,
                context_usage=context_usage if isinstance(context_usage, dict) and context_usage else None,
                extra_fields={
                    "interaction_type": "askuserquestion",
                    "question_id": question_id,
                    "question_prompt": prompt,
                    "question_options": options,
                    "question_allow_free_text": allow_free_text,
                    "question_required": required,
                    "question_pending": True,
                },
            )
            self._session_manager.save_runtime_state(
                state.session_id,
                {
                    "version": 1,
                    "state": "awaiting_user_answer",
                    **({"trace_run_id": state.run_id} if self.config.trace_dir is not None else {}),
                    "assistant_message_id": message_id,
                    "pending_question": {
                        "question_id": question_id,
                        "message_id": message_id,
                        "prompt": prompt,
                        "options": options,
                        "allow_free_text": allow_free_text,
                        "required": required,
                    },
                },
            )
            return

        if event_type == "ask_user_answer_received":
            question_id = str(event.get("question_id") or "").strip()
            message_id = state.question_message_ids.get(question_id)
            if message_id:
                self._session_manager.update_message(
                    state.session_id,
                    message_id,
                    lambda current: {**current, "question_pending": False},
                )
            self._persist_answer_message_for_question(question_id=question_id, state=state)
            saved = self._session_manager.load_runtime_state(state.session_id)
            if (saved and saved.get("state") == "awaiting_user_answer"
                    and saved.get("assistant_message_id") == message_id):
                self._session_manager.delete_runtime_state(state.session_id, expected=saved)

    def _clear_streaming_state(self, state: _StreamPersistenceState, *, interrupted: bool = False) -> None:
        if self._session_manager is None:
            return
        saved = self._session_manager.load_runtime_state(state.session_id)
        if not saved or saved.get("state") != "streaming" or saved.get("run_id") != state.run_id:
            return
        if interrupted:
            message_id = saved.get("assistant_message_id")
            if isinstance(message_id, str) and message_id.strip():
                self._session_manager.update_message(
                    state.session_id, message_id,
                    lambda current: {**current, "completion_state": "interrupted"},
                )
        self._session_manager.delete_runtime_state(state.session_id, expected=saved)

    def _durable_store(self) -> RunStore:
        if self.agent.run_store is None:
            raise DurableRunError("Durable execution is disabled.")
        return self.agent.run_store

    def list_runs(self, *, incomplete_only: bool = False) -> list[RunState]:
        """Discover durable runs in this workspace and active session."""
        return self._durable_store().list_runs(self.session_id, incomplete_only=incomplete_only)

    def list_run_steps(self, run_id: str) -> list[StepState]:
        """Inspect the ordered, committed journal for a run in this session."""
        return self._durable_store().list_steps(self.session_id, run_id)

    def pause_run(self, run_id: str) -> bool:
        """Request a pause at the next safe boundary of an active run."""
        return self._durable_store().pause(self.session_id, run_id)

    def resolve_run_step(self, run_id: str, step_id: str, output: str, *, is_error: bool = False) -> None:
        """Record the verified outcome of an ambiguous step without running the tool."""
        if not isinstance(output, str) or type(is_error) is not bool:
            raise ValueError("Recovery requires tool result text and a boolean error flag.")
        with self._durable_store().claim(self.session_id, run_id) as journal:
            journal.resolve(step_id, output, is_error=is_error)

    def abandon_run(self, run_id: str) -> None:
        """Close an unfinished run after the active owner exits."""
        with self._durable_store().claim(self.session_id, run_id) as journal:
            journal.abandon()
            if self._session_manager is not None:
                saved = self._session_manager.load_runtime_state(self.session_id)
                if saved and saved.get("durable_run_id") == journal.run_id:
                    message_id = saved.get("assistant_message_id")
                    if message_id:
                        self._session_manager.update_message(
                            self.session_id, message_id, lambda message: {**message, "question_pending": False},
                        )
                    self._session_manager.delete_runtime_state(self.session_id, expected=saved)

    async def resume_run(
        self,
        run_id: str,
        *,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        """Continue a durable run in the active session using current tool policy."""
        self._durable_store()
        if self._active_stream is not None or self._active_durable_segment:
            raise RuntimeError("Generation is already running on this bot")
        prev_stream = self.agent.stream_enabled
        self.agent.set_stream_enabled(False)
        try:
            messages, usage = await self._run_durable_once(
                prompt=None, interrupt_queue=None, reasoning_effort=reasoning_effort, resume_run_id=run_id,
            )
        finally:
            self.agent.set_stream_enabled(prev_stream)
        return ChatResult(self.session_id, messages, _last_assistant_text(messages), usage)

    def resume_run_stream(
        self,
        run_id: str,
        *,
        reasoning_effort: str | None = None,
        stop_on_ask_user_question: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Continue a durable run and yield the events it produces."""
        self._durable_store()
        return self._stream_events(
            None, None, reasoning_effort, stop_on_ask_user_question, resume_run_id=run_id,
        )

    def _file_checkpoint_store(self) -> GitFileCheckpoints:
        if self.agent.file_checkpoints is None:
            raise FileCheckpointError("File checkpoints are disabled.")
        return self.agent.file_checkpoints

    async def list_file_checkpoints(self) -> list[FileCheckpoint]:
        """List retained file checkpoints for this workspace and app home."""
        return await asyncio.to_thread(self._file_checkpoint_store().list)

    async def diff_file_checkpoint(self, checkpoint_id: str) -> str:
        """Show the recorded change for one file."""
        store = self._file_checkpoint_store()
        return await asyncio.to_thread(store.diff, checkpoint_id)

    async def rollback_file_checkpoint(self, checkpoint_id: str) -> bool:
        """Restore one unchanged target or return False for a consumed checkpoint."""
        store = self._file_checkpoint_store()
        return await finish_file_operation(lambda: store.rollback(checkpoint_id))

    def list_sessions(self) -> list[dict[str, Any]]:
        if self._session_manager is None:
            return []
        sessions = []
        for meta in self._session_manager.list_all():
            sessions.append(
                {
                    "session_id": meta.session_id,
                    "path": str(meta.path),
                    "created_at": meta.created_at.isoformat(),
                    "modified_at": meta.modified_at.isoformat(),
                    "message_count": meta.message_count,
                    "preview": meta.preview,
                }
            )
        return sessions

    def delete_session(self, session_id: str) -> bool:
        if self._session_manager is None:
            return False
        if self._active_stream is not None and self._active_stream.session_id == session_id:
            raise RuntimeError("Cannot delete a session while its stream is running")
        store = self.agent.run_store
        with store.guard_session_mutation(session_id) if store is not None else nullcontext():
            ok = self._session_manager.delete(session_id)
        if ok and session_id == self.session_id:
            self.create_session()
        return ok

    def register_tool(self, tool: ToolInput) -> None:
        """Register a tool (BaseTool, Python function, or ToolSpec dict)."""
        if self.agent.restricted_workspace:
            raise ValueError("Custom Python tools are unavailable in a restricted Docker workspace")
        if isinstance(tool, BaseTool):
            self.agent.tool_registry.register(tool)
            return

        if callable(tool):
            from ..tools.function import FunctionTool

            self.agent.tool_registry.register(FunctionTool(tool))
            return

        if isinstance(tool, dict):
            func = tool.get("func")
            if not callable(func):
                raise TypeError("ToolSpec dict must include a callable 'func'")
            from ..tools.function import FunctionTool

            self.agent.tool_registry.register(
                FunctionTool(
                    func,
                    name=tool.get("name"),
                    description=tool.get("description"),
                    input_schema=tool.get("input_schema"),
                )
            )
            return

        raise TypeError(f"Unsupported tool type: {type(tool).__name__}")

    async def connect_mcp(self) -> dict[str, Exception | None]:
        return await self.agent.connect_mcp_servers()

    def cancel(self) -> None:
        """Best-effort cancel of the active generation (if any)."""
        q = self._active_interrupt_queue
        if q is not None:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._cancel_tool_approvals()
        pending = self._pending_question_future
        if pending is not None and not pending.done():
            pending.set_exception(UserInterruptedError("Interrupted while waiting for user answer"))
            return

        if self._active_stream is not None:
            self._clear_streaming_state(self._active_stream, interrupted=True)

    def _cancel_tool_approvals(self) -> None:
        if self._active_stream is not None:
            self._active_stream.approvals_cancelled = True
        for future in self._pending_tool_approvals.values():
            if not future.done():
                future.set_result(False)

    async def _wait_for_tool_approval(self, request: ToolApprovalRequest) -> bool:
        state = self._active_stream
        if state is not None and state.approvals_cancelled:
            return False
        if self._tool_approval_handler is not None:
            approved = await self._tool_approval_handler(request)
            return approved is True and not (state is not None and state.approvals_cancelled)
        if state is None:
            return False
        future = asyncio.get_running_loop().create_future()
        self._pending_tool_approvals[request.approval_id] = future
        try:
            await self._event_router.emit({
                "type": "tool_approval_request",
                "session_id": state.session_id,
                "approval_id": request.approval_id,
                "tool_name": request.tool_name,
                "approval_actor": request.actor,
                "approval_preview": request.preview,
            })
            approved = await future
            return approved and not state.approvals_cancelled
        finally:
            self._pending_tool_approvals.pop(request.approval_id, None)

    def submit_tool_approval(self, approval_id: str, approved: bool) -> None:
        """Resolve one live approval. A stale or consumed ID raises ValueError."""
        future = self._pending_tool_approvals.get(approval_id)
        if self._active_stream is None or self._active_stream.approvals_cancelled or future is None or future.done():
            raise ValueError("No live tool approval matches this ID")
        if type(approved) is not bool:
            raise ValueError("Tool approval decision must be boolean")
        future.set_result(approved)

    async def _wait_for_user_answer(self, question: dict[str, Any]) -> dict[str, Any]:
        if self._active_stream is not None and self._active_stream.stop_on_ask_user_question:
            raise UserInterruptedError("Paused for user answer")
        if self._pending_question_future is not None and not self._pending_question_future.done():
            raise RuntimeError("Another askuserquestion interaction is already pending")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_question = dict(question)
        self._pending_question_future = future
        try:
            return await future
        finally:
            self._pending_question = None
            self._pending_question_future = None

    def pending_question(self) -> dict[str, Any] | None:
        if self._pending_question is None:
            return None
        return dict(self._pending_question)

    async def submit_user_answer(
        self,
        *,
        question_id: str,
        answer_text: str,
        selected_option_value: str | None = None,
    ) -> None:
        pending = self._pending_question
        future = self._pending_question_future
        if pending is None or future is None:
            raise ValueError("No askuserquestion interaction is pending")
        if str(pending.get("question_id") or "") != str(question_id):
            raise ValueError("Pending question id does not match")
        if future.done():
            raise ValueError("Pending question is already resolved")
        future.set_result(
            {
                "answer_text": answer_text,
                "selected_option_value": selected_option_value,
            }
        )

    def stream_existing_messages(
        self,
        *,
        reasoning_effort: str | None = None,
        stop_on_ask_user_question: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Continue generation from the current persisted message history."""
        return self._stream_events(None, None, reasoning_effort, stop_on_ask_user_question)

    async def resume_pending_question_stream(
        self,
        *,
        question_id: str,
        answer_text: str,
        selected_option_value: str | None = None,
        reasoning_effort: str | None = None,
        parent_run_id: str | None = None,
        durable_run_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Resume a persisted pending-question turn until completion or the next pause."""
        durable_answer = {
            "question_id": question_id, "answer_text": answer_text,
            "selected_option_value": selected_option_value,
        } if durable_run_id is not None else None
        async for event in self._stream_events(
            None, None, reasoning_effort, True, parent_run_id=parent_run_id,
            resume_run_id=durable_run_id, durable_answer=durable_answer,
        ):
            yield event

    async def chat(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        """Non-streaming chat call. Use :meth:`stream` for incremental deltas."""
        if session_id is not None and session_id != self.session_id:
            self.set_session(session_id)

        if self._active_stream is not None or self._active_durable_segment:
            raise RuntimeError("Generation is already running on this bot")
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        await self._event_router.set_queue(queue)
        prev_stream = self.agent.stream_enabled
        self.agent.set_stream_enabled(False)

        try:
            result_messages, run_usage = await self._run_once(
                prompt,
                interrupt_queue=None,
                reasoning_effort=reasoning_effort,
            )
        finally:
            self.agent.set_stream_enabled(prev_stream)
            await self._event_router.set_queue(None)

        usage: dict[str, int] | None = run_usage
        drained: list[StreamEvent] = []
        while True:
            try:
                drained.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for event in reversed(drained):
            if event.get("type") == "assistant_end":
                candidate = event.get("usage") or {}
                if candidate:
                    usage = dict(candidate)
                break

        assistant_text = _last_assistant_text(result_messages)
        return ChatResult(
            session_id=self.session_id,
            messages=result_messages,
            assistant_text=assistant_text,
            usage=usage,
        )

    def chat_sync(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        if _has_running_loop():
            raise RuntimeError("chat_sync() cannot be used inside a running event loop; use 'await chat()'.")
        return asyncio.run(self.chat(prompt, session_id=session_id, reasoning_effort=reasoning_effort))

    def stream(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        reasoning_effort: str | None = None,
        stop_on_ask_user_question: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat call yielding structured events."""
        return self._stream_events(prompt, session_id, reasoning_effort, stop_on_ask_user_question)

    async def _stream_events(
        self, prompt: str | None, session_id: str | None, reasoning_effort: str | None,
        stop_on_ask_user_question: bool,
        *,
        parent_run_id: str | None = None,
        resume_run_id: str | None = None,
        durable_answer: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if session_id == "":
            if self._active_stream is not None:
                raise RuntimeError("A stream is already running on this bot")
            session_id = self._resolve_session_id(session_id)
        state = _StreamPersistenceState(
            session_id=self.session_id if session_id is None else session_id,
            stop_on_ask_user_question=stop_on_ask_user_question,
        )
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=256)
        interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        detached = False

        async def produce() -> None:
            if self._active_stream is not None or self._active_durable_segment:
                raise RuntimeError("Generation is already running on this bot")
            with own_stream(self._session_manager, state.session_id, state.run_id):
                if state.session_id != self.session_id:
                    self.set_session(state.session_id)
                self._active_stream = state
                self._active_interrupt_queue = interrupt_queue
                prev_stream = self.agent.stream_enabled
                try:
                    trace_parent_run_id = parent_run_id
                    if (self.agent.run_store is None and self.config.trace_dir is not None and prompt is None
                            and trace_parent_run_id is None and self._session_manager is not None):
                        saved = self._session_manager.load_runtime_state(state.session_id)
                        if saved and saved.get("state") == "awaiting_user_answer":
                            trace_parent_run_id = saved.get("trace_run_id")
                    await self._event_router.set_queue(None if detached else queue)
                    await self._event_router.set_tap(
                        lambda event: self._handle_stream_persistence_event(event, state)
                    )
                    self.agent.set_stream_enabled(True)
                    if self.agent.run_store is not None:
                        await self._run_durable_once(
                            prompt=prompt, interrupt_queue=interrupt_queue, reasoning_effort=reasoning_effort,
                            run_id=state.run_id, parent_run_id=parent_run_id,
                            resume_run_id=resume_run_id, durable_answer=durable_answer,
                        )
                    elif resume_run_id is not None:
                        raise DurableRunError("Durable execution is disabled.")
                    elif prompt is None:
                        await self._run_existing_messages_once(
                            interrupt_queue=interrupt_queue, reasoning_effort=reasoning_effort,
                            parent_run_id=trace_parent_run_id,
                        )
                    else:
                        await self._run_once(
                            prompt, interrupt_queue=interrupt_queue, reasoning_effort=reasoning_effort,
                        )
                except asyncio.CancelledError:
                    self._clear_streaming_state(state, interrupted=True)
                    raise
                except (UserInterruptedError, DurableRunPaused):
                    self._clear_streaming_state(state, interrupted=True)
                except Exception as exc:
                    await self._event_router.emit({
                        "type": "system", "message": "Error during generation.",
                        "data": {"error": str(exc)},
                    })
                    raise
                finally:
                    try:
                        self.agent.set_stream_enabled(prev_stream)
                        await self._event_router.set_tap(None)
                        if self._session_manager is not None:
                            self.messages = self._session_manager.load(state.session_id)
                    finally:
                        self._cancel_tool_approvals()
                        self._active_interrupt_queue = None
                        self._active_stream = None

        task = asyncio.create_task(produce(), name=f"minibot-stream-{state.session_id}")
        state.producer = task
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                yield event
            await task
        finally:
            detached = True
            if self._active_stream is state and self._tool_approval_handler is None:
                self._cancel_tool_approvals()
            self._event_router.detach_queue(queue)

    def stream_sync(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        reasoning_effort: str | None = None,
        stop_on_ask_user_question: bool = False,
    ) -> Iterator[StreamEvent]:
        if _has_running_loop():
            raise RuntimeError("stream_sync() cannot be used inside a running event loop; use 'async for e in stream()'.")
        loop = asyncio.new_event_loop()
        agen = self.stream(
            prompt,
            session_id=session_id,
            reasoning_effort=reasoning_effort,
            stop_on_ask_user_question=stop_on_ask_user_question,
        )
        try:
            while True:
                try:
                    event = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield event
        finally:
            try:
                state = self._active_stream
                producer = state.producer if state is not None else None
                if producer is not None and producer.get_loop() is loop:
                    loop.run_until_complete(self._event_router.set_queue(None))
                    producer.cancel()
                    loop.run_until_complete(asyncio.gather(producer, return_exceptions=True))
                tasks = asyncio.all_tasks(loop)
                for task in tasks:
                    task.cancel()
                if tasks:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                loop.run_until_complete(agen.aclose())
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    async def _run_existing_messages_once(
        self,
        *,
        interrupt_queue: "asyncio.Queue[None] | None",
        reasoning_effort: str | None,
        parent_run_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
        pre_len = len(self.messages)
        usage: dict[str, int] | None = None
        interrupted_error: UserInterruptedError | None = None
        parent_user_message_id = _last_user_message_id(self.messages)
        trace_options: dict[str, Any] = {}
        if self.config.trace_dir is not None:
            trace_options = {
                "run_id": self._active_stream.run_id if self._active_stream is not None else None,
                "parent_run_id": parent_run_id,
            }

        try:
            await self.agent.run_loop(
                self.messages,
                interrupt_queue=interrupt_queue,
                reasoning_effort=reasoning_effort,
                **trace_options,
            )
        except UserInterruptedError as exc:
            interrupted_error = exc
        except Exception as exc:
            if isinstance(exc, (RuntimeBudgetExceeded, ContextWindowExceeded)) and self._active_stream is not None:
                self._clear_streaming_state(self._active_stream)
            error_msg = {
                "role": "assistant",
                "content": f"[Error: {exc}]",
                "message_id": _new_message_id(),
                "parent_user_message_id": parent_user_message_id,
            }
            self.messages.append(error_msg)

        new_messages = self.messages[pre_len:] if len(self.messages) >= pre_len else self.messages
        if self._session_manager is not None:
            persisted_ids = self._persisted_ids_for_active_run()
            for msg in new_messages:
                message_id = msg.get("message_id")
                if isinstance(message_id, str) and message_id in persisted_ids:
                    continue
                self._session_manager.append_message(self.session_id, msg)

        usage = _last_assistant_usage(new_messages) or _last_assistant_usage(self.messages)
        if interrupted_error is not None:
            raise interrupted_error
        return self.messages, usage

    async def _run_durable_once(
        self,
        *,
        prompt: str | None,
        interrupt_queue: "asyncio.Queue[None] | None",
        reasoning_effort: str | None,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        resume_run_id: str | None = None,
        durable_answer: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
        if self._active_durable_segment:
            raise RuntimeError("Generation is already running on this bot")
        self._active_durable_segment = True
        session_id = self.session_id
        working = list(self.messages)

        def seed() -> list[dict[str, Any]]:
            messages = self._session_manager.load(session_id) if self._session_manager is not None else working[:]
            if prompt is not None:
                messages.append({"role": "user", "content": prompt, "message_id": _new_message_id()})
            return messages

        def publish(messages: list[dict[str, Any]]) -> None:
            if self._session_manager is None:
                self.messages = messages
                return
            self._session_manager.store.project_durable_messages(session_id, messages)
            saved = self._session_manager.load_runtime_state(session_id)
            if saved and saved.get("state") == "awaiting_user_answer":
                question = saved.get("pending_question") or {}
                if any(message.get("answer_to_question_id") == question.get("question_id") for message in messages
                       if message.get("answer_to_question_id")):
                    self._session_manager.delete_runtime_state(session_id, expected=saved)

        try:
            result = await self.agent.run_loop(
                working, interrupt_queue=interrupt_queue, reasoning_effort=reasoning_effort,
                run_id=run_id, parent_run_id=parent_run_id, resume_run_id=resume_run_id,
                durable_answer=durable_answer, seed_messages=seed, publish_messages=publish,
            )
            return result, _last_assistant_usage(result)
        finally:
            self._active_durable_segment = False
            if self._session_manager is not None:
                self.messages = self._session_manager.load(session_id)

    async def _run_once(
        self,
        prompt: str,
        *,
        interrupt_queue: "asyncio.Queue[None] | None",
        reasoning_effort: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
        if self.agent.run_store is not None:
            return await self._run_durable_once(
                prompt=prompt, interrupt_queue=interrupt_queue, reasoning_effort=reasoning_effort,
            )
        user_msg = {"role": "user", "content": prompt, "message_id": _new_message_id()}
        self.messages.append(user_msg)
        if self._session_manager is not None:
            self._session_manager.append_message(self.session_id, user_msg)
        return await self._run_existing_messages_once(
            interrupt_queue=interrupt_queue,
            reasoning_effort=reasoning_effort,
        )
