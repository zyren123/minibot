"""Event primitives for streaming/chat integrations.

This module defines a small, stable event shape that SDK, server, and UI layers
can share without depending on terminal rendering.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, NotRequired, Protocol, TypedDict


EventType = Literal[
    "assistant_start",
    "assistant_reasoning_delta",
    "assistant_delta",
    "assistant_end",
    "todo_snapshot",
    "tool_call",
    "tool_result",
    "system",
]


class StreamEvent(TypedDict, total=False):
    """A structured event emitted by the agent runtime."""

    type: EventType
    session_id: str

    # Assistant streaming
    message_id: str
    parent_user_message_id: str | None
    delta_text: str
    reasoning_text: str
    reasoning: str
    content: str
    finish_reason: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, int]
    todo: dict[str, Any]

    # Tool execution
    tool_call_id: str
    tool_name: str
    tool_args: dict[str, Any]
    tool_output: str
    is_error: bool
    note: str

    # System / status
    message: str
    data: dict[str, Any]


class EventSink(Protocol):
    async def emit(self, event: StreamEvent) -> None:
        """Consume one event."""


class NullEventSink:
    async def emit(self, event: StreamEvent) -> None:  # noqa: ARG002
        return None


class AsyncQueueEventSink:
    """Event sink backed by an asyncio.Queue."""

    def __init__(self, queue: "asyncio.Queue[StreamEvent]") -> None:
        self.queue = queue

    async def emit(self, event: StreamEvent) -> None:
        await self.queue.put(event)
