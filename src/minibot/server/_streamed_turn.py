from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anyio import CancelScope

from ..events import StreamEvent
from ..sdk._active_streams import StreamReader

if TYPE_CHECKING:
    from ..sdk import Minibot


@dataclass(frozen=True)
class TurnSource:
    bot: Minibot
    events: AsyncIterator[StreamEvent]

    def cancel(self) -> None:
        if isinstance(self.events, StreamReader):
            self.events.cancel()
        else:
            self.bot.cancel()

    async def close(self) -> None:
        if isinstance(self.events, AsyncGenerator):
            await self.events.aclose()


class StreamedTurn:
    def __init__(
        self, lock: asyncio.Lock, prepare: Callable[[], Awaitable[TurnSource]],
    ) -> None:
        self._lock = lock
        self._prepare = prepare
        self._source: TurnSource | None = None
        self._cancel_on_close = False

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        async with self._lock:
            try:
                yield
            finally:
                if self._source is not None:
                    with CancelScope(shield=True):
                        try:
                            if self._cancel_on_close:
                                self._source.cancel()
                        finally:
                            await self._source.close()

    async def prepare(self) -> TurnSource:
        if self._source is None:
            self._source = await self._prepare()
        return self._source

    def __aiter__(self) -> StreamedTurn:
        return self

    async def __anext__(self) -> StreamEvent:
        source = await self.prepare()
        self._cancel_on_close = True
        try:
            return await anext(source.events)
        except Exception:
            self._cancel_on_close = False
            raise
