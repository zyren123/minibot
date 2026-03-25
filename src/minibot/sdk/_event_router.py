"""Internal event routing for SDK streaming."""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Awaitable, Callable

from ..events import EventSink, StreamEvent


class RouterEventSink(EventSink):
    """Routes emitted events into the currently active queue (if any)."""

    def __init__(self) -> None:
        self._queue: "asyncio.Queue[StreamEvent] | None" = None
        self._tap: "Callable[[StreamEvent], Awaitable[Any] | Any] | None" = None
        self._lock = threading.Lock()

    async def set_queue(self, queue: "asyncio.Queue[StreamEvent] | None") -> None:
        with self._lock:
            self._queue = queue

    async def set_tap(
        self,
        tap: "Callable[[StreamEvent], Awaitable[Any] | Any] | None",
    ) -> None:
        with self._lock:
            self._tap = tap

    async def emit(self, event: StreamEvent) -> None:
        with self._lock:
            queue = self._queue
            tap = self._tap
        if tap is not None:
            result = tap(event)
            if inspect.isawaitable(result):
                await result
        if queue is None:
            return
        await queue.put(event)
