"""Rich availability and shared console."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def rich_enabled() -> bool:
    env = os.environ.get("MINIBOT_RICH", "1").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    try:
        import rich  # noqa: F401
    except Exception:
        return False
    return True


_CONSOLE: "Console | None" = None


def get_console() -> "Console":
    global _CONSOLE
    if _CONSOLE is not None:
        return _CONSOLE
    from rich.console import Console

    _CONSOLE = Console(highlight=False, soft_wrap=True, emoji=False)
    return _CONSOLE

