"""Programmatic SDK wrapper for Minibot."""

from __future__ import annotations

import asyncio
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

        self.agent = Agent(
            config=self.config,
            session_id=self.session_id,
            extra_system_prompt=system_prompt,
            event_sink=self._event_router,
            skills_dir=skills_dir,
            disabled_skills=disabled_skills,
            allow_subagent_delegation=allow_subagent_delegation,
            allow_team_tools=allow_team_tools,
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
        if q is None:
            return
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            return

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
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat call yielding structured events."""
        if session_id is not None and session_id != self.session_id:
            self.set_session(session_id)

        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=256)
        await self._event_router.set_queue(queue)
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

        run_exc: BaseException | None = None

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
                yield event
        finally:
            self._active_interrupt_queue = None
            self.agent.set_stream_enabled(prev_stream)
            await self._event_router.set_queue(None)

        if run_exc is None:
            _messages, _usage = run_task.result()
            return

        if isinstance(run_exc, UserInterruptedError):
            interrupted = {
                "role": "assistant",
                "content": "[Generation interrupted]",
                "message_id": _new_message_id(),
                "parent_user_message_id": _last_user_message_id(self.messages),
            }
            self.messages.append(interrupted)
            if self._session_manager is not None:
                self._session_manager.append_message(self.session_id, interrupted)
            yield {"type": "system", "message": "Generation interrupted."}
            return

        yield {"type": "system", "message": "Error during generation.", "data": {"error": str(run_exc)}}
        raise run_exc

    def stream_sync(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        reasoning_effort: str | None = None,
    ) -> Iterator[StreamEvent]:
        if _has_running_loop():
            raise RuntimeError("stream_sync() cannot be used inside a running event loop; use 'async for e in stream()'.")
        loop = asyncio.new_event_loop()
        try:
            agen = self.stream(prompt, session_id=session_id, reasoning_effort=reasoning_effort)
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

        pre_len = len(self.messages)
        usage: dict[str, int] | None = None

        try:
            await self.agent.run_loop(
                self.messages,
                interrupt_queue=interrupt_queue,
                reasoning_effort=reasoning_effort,
            )
        except UserInterruptedError:
            raise
        except Exception as exc:
            error_msg = {
                "role": "assistant",
                "content": f"[Error: {exc}]",
                "message_id": _new_message_id(),
                "parent_user_message_id": user_msg["message_id"],
            }
            self.messages.append(error_msg)

        new_messages = self.messages[pre_len:] if len(self.messages) >= pre_len else self.messages
        if self._session_manager is not None:
            for msg in new_messages:
                self._session_manager.append_message(self.session_id, msg)

        # Best-effort extract latest usage from emitted events (assistant_end includes it).
        # If no sink/consumer is attached, usage will remain None.
        usage = _last_assistant_usage(new_messages) or _last_assistant_usage(self.messages)
        return self.messages, usage
