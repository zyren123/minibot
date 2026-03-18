"""Internal event routing for SDK streaming."""

from __future__ import annotations

import asyncio
import threading

from ..events import EventSink, StreamEvent


class RouterEventSink(EventSink):
    """Routes emitted events into the currently active queue (if any)."""

    def __init__(self) -> None:
        self._queue: "asyncio.Queue[StreamEvent] | None" = None
        self._lock = threading.Lock()

    async def set_queue(self, queue: "asyncio.Queue[StreamEvent] | None") -> None:
        with self._lock:
            self._queue = queue

    async def emit(self, event: StreamEvent) -> None:
        with self._lock:
            queue = self._queue
        if queue is None:
            return
        await queue.put(event)
