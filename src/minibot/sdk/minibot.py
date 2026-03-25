"""Programmatic SDK wrapper for Minibot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import uuid
from typing import Any, AsyncIterator, Callable, Iterable, Iterator

from ..agent import Agent, UserInterruptedError
from ..config import Config, load_config
from ..events import StreamEvent
from ..session.manager import SessionManager
from ..tools.base import BaseTool

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
    persisted_message_ids: set[str] = field(default_factory=set)
    assistant_contents: dict[str, str] = field(default_factory=dict)
    assistant_reasoning: dict[str, str] = field(default_factory=dict)
    assistant_parent_ids: dict[str, str | None] = field(default_factory=dict)
    question_message_ids: dict[str, str] = field(default_factory=dict)


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
    ) -> None:
        if config is None:
            resolved_workdir = Path(workdir).resolve() if workdir is not None else None
            config = load_config(workdir=resolved_workdir)

        if sessions_dir is not None:
            config.session.sessions_dir = str(Path(sessions_dir).expanduser().resolve())

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
        self._persisted_message_ids_for_active_run: set[str] | None = None

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
        self.session_id = self._resolve_session_id(session_id)
        self.agent.reset_session(self.session_id)
        self.messages = (
            self._session_manager.load(self.session_id)
            if self._session_manager is not None
            else []
        )

    def create_session(self) -> str:
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
        return self._persisted_message_ids_for_active_run or set()

    def _save_streaming_runtime_state(self, *, message_id: str, parent_user_message_id: str | None) -> None:
        if self._session_manager is None:
            return
        payload: dict[str, Any] = {
            "version": 1,
            "state": "streaming",
            "assistant_message_id": message_id,
        }
        if parent_user_message_id:
            payload["parent_user_message_id"] = parent_user_message_id
        self._session_manager.save_runtime_state(self.session_id, payload)

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
                message["tool_calls"] = [dict(item) for item in tool_calls]
            if extra_fields:
                message.update(extra_fields)
            return message

        if message_id in state.persisted_message_ids:
            self._session_manager.update_message(
                self.session_id,
                message_id,
                lambda current: build_message(dict(current)),
            )
            return

        self._session_manager.append_message(self.session_id, build_message())
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
            self._session_manager.append_message(self.session_id, dict(message))
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
                self._session_manager.delete_runtime_state(self.session_id)
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
                self.session_id,
                {
                    "version": 1,
                    "state": "awaiting_user_answer",
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
                    self.session_id,
                    message_id,
                    lambda current: {**current, "question_pending": False},
                )
            self._persist_answer_message_for_question(question_id=question_id, state=state)
            self._session_manager.delete_runtime_state(self.session_id)

    async def _finalize_stream_run(
        self,
        run_task: "asyncio.Task[tuple[list[dict[str, Any]], dict[str, int] | None]]",
        state: _StreamPersistenceState,
    ) -> None:
        try:
            await run_task
        except UserInterruptedError:
            if self._session_manager is not None:
                runtime_state = self._session_manager.load_runtime_state(self.session_id)
                if runtime_state is None or runtime_state.get("state") != "awaiting_user_answer":
                    assistant_message_id = None
                    if isinstance(runtime_state, dict):
                        raw_message_id = runtime_state.get("assistant_message_id")
                        if isinstance(raw_message_id, str) and raw_message_id.strip():
                            assistant_message_id = raw_message_id
                    if assistant_message_id is None and state.assistant_contents:
                        assistant_message_id = next(reversed(state.assistant_contents))
                    if assistant_message_id is not None:
                        self._session_manager.update_message(
                            self.session_id,
                            assistant_message_id,
                            lambda current: {**current, "completion_state": "interrupted"},
                        )
                    self._session_manager.delete_runtime_state(self.session_id)
        except Exception:
            pass
        finally:
            if self._session_manager is not None:
                self.messages = self._session_manager.load(self.session_id)
            self._persisted_message_ids_for_active_run = None
            await self._event_router.set_tap(None)

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
        ok = self._session_manager.delete(session_id)
        if ok and session_id == self.session_id:
            self.create_session()
        return ok

    def register_tool(self, tool: ToolInput) -> None:
        """Register a tool (BaseTool, Python function, or ToolSpec dict)."""
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
        pending = self._pending_question_future
        if pending is not None and not pending.done():
            pending.set_exception(UserInterruptedError("Interrupted while waiting for user answer"))
            return

        if self._session_manager is None:
            return

        runtime_state = self._session_manager.load_runtime_state(self.session_id)
        if runtime_state is None or runtime_state.get("state") != "streaming":
            return
        assistant_message_id = runtime_state.get("assistant_message_id")
        if isinstance(assistant_message_id, str) and assistant_message_id.strip():
            self._session_manager.update_message(
                self.session_id,
                assistant_message_id,
                lambda current: {**current, "completion_state": "interrupted"},
            )
        self._session_manager.delete_runtime_state(self.session_id)

    async def _wait_for_user_answer(self, question: dict[str, Any]) -> dict[str, Any]:
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

    async def stream_existing_messages(
        self,
        *,
        reasoning_effort: str | None = None,
        stop_on_ask_user_question: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Continue generation from the current persisted message history."""
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=256)
        await self._event_router.set_queue(queue)
        stream_state = _StreamPersistenceState()
        self._persisted_message_ids_for_active_run = stream_state.persisted_message_ids
        await self._event_router.set_tap(
            lambda event: self._handle_stream_persistence_event(event, stream_state)
        )
        prev_stream = self.agent.stream_enabled
        self.agent.set_stream_enabled(True)

        interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._active_interrupt_queue = interrupt_queue

        run_task = asyncio.create_task(
            self._run_existing_messages_once(
                interrupt_queue=interrupt_queue,
                reasoning_effort=reasoning_effort,
            ),
            name=f"minibot-resume-{self.session_id}",
        )
        finalize_task = asyncio.create_task(
            self._finalize_stream_run(run_task, stream_state),
            name=f"minibot-resume-finalize-{self.session_id}",
        )

        run_exc: BaseException | None = None
        pause_requested = False

        try:
            while True:
                if run_task.done():
                    run_exc = run_task.exception()
                if run_task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if (
                    stop_on_ask_user_question
                    and not pause_requested
                    and event.get("type") == "ask_user_question"
                ):
                    pause_requested = True
                    self.cancel()
                yield event
        finally:
            self._active_interrupt_queue = None
            self.agent.set_stream_enabled(prev_stream)
            await self._event_router.set_queue(None)

        if run_exc is None:
            _messages, _usage = run_task.result()
            await finalize_task
            return

        if isinstance(run_exc, UserInterruptedError):
            await finalize_task
            return

        await finalize_task
        yield {"type": "system", "message": "Error during generation.", "data": {"error": str(run_exc)}}
        raise run_exc

    async def resume_pending_question_stream(
        self,
        *,
        question_id: str,  # noqa: ARG002
        answer_text: str,  # noqa: ARG002
        selected_option_value: str | None = None,  # noqa: ARG002
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Resume a persisted pending-question turn until completion or the next pause."""
        async for event in self.stream_existing_messages(
            reasoning_effort=reasoning_effort,
            stop_on_ask_user_question=True,
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

    async def stream(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        reasoning_effort: str | None = None,
        stop_on_ask_user_question: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat call yielding structured events."""
        if session_id is not None and session_id != self.session_id:
            self.set_session(session_id)

        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=256)
        await self._event_router.set_queue(queue)
        stream_state = _StreamPersistenceState()
        self._persisted_message_ids_for_active_run = stream_state.persisted_message_ids
        await self._event_router.set_tap(
            lambda event: self._handle_stream_persistence_event(event, stream_state)
        )
        prev_stream = self.agent.stream_enabled
        self.agent.set_stream_enabled(True)

        interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._active_interrupt_queue = interrupt_queue

        run_task = asyncio.create_task(
            self._run_once(
                prompt,
                interrupt_queue=interrupt_queue,
                reasoning_effort=reasoning_effort,
            ),
            name=f"minibot-run-{self.session_id}",
        )
        finalize_task = asyncio.create_task(
            self._finalize_stream_run(run_task, stream_state),
            name=f"minibot-stream-finalize-{self.session_id}",
        )

        run_exc: BaseException | None = None
        pause_requested = False

        try:
            while True:
                if run_task.done():
                    run_exc = run_task.exception()
                if run_task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if (
                    stop_on_ask_user_question
                    and not pause_requested
                    and event.get("type") == "ask_user_question"
                ):
                    pause_requested = True
                    self.cancel()
                yield event
        finally:
            self._active_interrupt_queue = None
            self.agent.set_stream_enabled(prev_stream)
            await self._event_router.set_queue(None)

        if run_exc is None:
            _messages, _usage = run_task.result()
            await finalize_task
            return

        if isinstance(run_exc, UserInterruptedError):
            await finalize_task
            return

        await finalize_task
        yield {"type": "system", "message": "Error during generation.", "data": {"error": str(run_exc)}}
        raise run_exc

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
        try:
            agen = self.stream(
                prompt,
                session_id=session_id,
                reasoning_effort=reasoning_effort,
                stop_on_ask_user_question=stop_on_ask_user_question,
            )
            while True:
                try:
                    event = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield event
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    async def _run_existing_messages_once(
        self,
        *,
        interrupt_queue: "asyncio.Queue[None] | None",
        reasoning_effort: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
        pre_len = len(self.messages)
        usage: dict[str, int] | None = None
        interrupted_error: UserInterruptedError | None = None
        parent_user_message_id = _last_user_message_id(self.messages)

        try:
            await self.agent.run_loop(
                self.messages,
                interrupt_queue=interrupt_queue,
                reasoning_effort=reasoning_effort,
            )
        except UserInterruptedError as exc:
            interrupted_error = exc
        except Exception as exc:
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

    async def _run_once(
        self,
        prompt: str,
        *,
        interrupt_queue: "asyncio.Queue[None] | None",
        reasoning_effort: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
        user_msg = {"role": "user", "content": prompt, "message_id": _new_message_id()}
        self.messages.append(user_msg)
        if self._session_manager is not None:
            self._session_manager.append_message(self.session_id, user_msg)
        return await self._run_existing_messages_once(
            interrupt_queue=interrupt_queue,
            reasoning_effort=reasoning_effort,
        )
