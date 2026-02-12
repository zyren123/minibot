"""ESC key interrupt monitor for interactive sessions."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress


class EscInterruptMonitor:
    """Monitor stdin for ESC and push interrupt signals into an async queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._enabled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fd: int | None = None
        self._termios = None
        self._tty = None
        self._old_attrs = None

    @property
    def queue(self) -> asyncio.Queue[None]:
        return self._queue

    def _supports_monitoring(self) -> bool:
        try:
            if not sys.stdin.isatty():
                return False
            fd = sys.stdin.fileno()
        except Exception:
            return False

        try:
            import termios  # type: ignore
            import tty  # type: ignore
        except Exception:
            return False

        self._fd = fd
        self._termios = termios
        self._tty = tty
        return True

    async def __aenter__(self) -> "EscInterruptMonitor":
        if not self._supports_monitoring():
            return self

        assert self._fd is not None
        assert self._termios is not None
        assert self._tty is not None

        self._loop = asyncio.get_running_loop()
        self._old_attrs = self._termios.tcgetattr(self._fd)
        self._tty.setcbreak(self._fd)
        self._loop.add_reader(self._fd, self._on_stdin_ready)
        self._enabled = True
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        if not self._enabled:
            return

        assert self._fd is not None
        assert self._termios is not None

        if self._loop is not None:
            with suppress(Exception):
                self._loop.remove_reader(self._fd)
        with suppress(Exception):
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._old_attrs)

        self._enabled = False

    def _on_stdin_ready(self) -> None:
        if not self._enabled or self._fd is None:
            return
        try:
            data = os.read(self._fd, 32)
        except OSError:
            return

        if not data:
            return

        if 0x1B in data:
            with suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)
