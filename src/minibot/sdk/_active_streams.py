from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from ..events import StreamEvent
from ..session.manager import SessionManager


@dataclass
class StreamCancellation:
    callback: Callable[[], None] | None = None


class StreamReader(AsyncGenerator[StreamEvent, None]):
    def __init__(self, events: Callable[[StreamCancellation], AsyncGenerator[StreamEvent, None]]):
        self._cancellation = StreamCancellation()
        self._events = events(self._cancellation)

    def cancel(self) -> None:
        cancel = self._cancellation.callback
        if cancel is not None:
            cancel()

    async def asend(self, value: None) -> StreamEvent:
        try:
            return await self._events.asend(value)
        except Exception:
            self._cancellation.callback = None
            raise

    async def athrow(self, *args: Any) -> StreamEvent:
        return await self._events.athrow(*args)

    async def aclose(self) -> None:
        try:
            await self._events.aclose()
        finally:
            self._cancellation.callback = None


_owners: dict[tuple[Path, str], tuple[str, asyncio.Task]] = {}
_lock = Lock()


def _live_owner(key: tuple[Path, str]) -> tuple[str, asyncio.Task] | None:
    owner = _owners.get(key)
    if owner and (owner[1].done() or owner[1].get_loop().is_closed()):
        del _owners[key]
        return None
    return owner


def is_active_stream(manager: SessionManager, session_id: str, run_id: object) -> bool:
    with _lock:
        owner = _live_owner((manager.sessions_dir, session_id))
        return owner is not None and owner[0] == run_id


@contextmanager
def own_stream(manager: SessionManager | None, session_id: str, run_id: str) -> Iterator[None]:
    if manager is None:
        yield
        return

    key = (manager.sessions_dir, session_id)
    task = asyncio.current_task()
    assert task is not None
    with _lock:
        if _live_owner(key) is not None:
            raise RuntimeError("A stream is already running for this session")
        _owners[key] = (run_id, task)
    try:
        yield
    finally:
        with _lock:
            if _owners.get(key) == (run_id, task):
                del _owners[key]
