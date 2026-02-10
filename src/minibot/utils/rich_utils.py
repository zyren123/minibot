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
    from .terminal import term_width

    desired_width = term_width()
    if _CONSOLE is not None and _CONSOLE.width == desired_width:
        return _CONSOLE
    from rich.console import Console

    # Avoid soft_wrap=True here; it disables wrapping/cropping and can cause
    # Rich panels/markdown to drop content on long lines.
    _CONSOLE = Console(highlight=False, soft_wrap=False, emoji=False, width=desired_width)
    return _CONSOLE
