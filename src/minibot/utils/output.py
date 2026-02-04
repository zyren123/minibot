"""Output formatting utilities."""

from __future__ import annotations

import io
import json
import os
import re
from contextlib import contextmanager
from typing import Iterator

from .rich_utils import get_console, rich_enabled
from .terminal import PanelStyle, clear_screen as clear_screen_fallback, panel, style, term_width


def print_assistant(content: str) -> None:
    """Print assistant content in a readable block."""
    content = content.rstrip()
    if not content:
        return
    if rich_enabled():
        from rich.markdown import Markdown
        from rich.panel import Panel

        console = get_console()
        console.print(
            Panel(
                Markdown(content),
                title="Assistant",
                border_style="bright_black",
                title_align="left",
            )
        )
        return
    print(panel("Assistant", content, width=term_width(), pstyle=PanelStyle()))


def print_system(message: str) -> None:
    """Print system/status messages."""
    if rich_enabled():
        console = get_console()
        console.print(f"[bright_black]{message}[/]")
        return
    print(style(message, fg="bright_black"))


def print_tool_call(line: str) -> None:
    """Print a tool-call header line."""
    if rich_enabled():
        console = get_console()
        console.print(f"[bold bright_cyan]{line}[/]")
        return
    print(style(line, fg="bright_cyan", bold=True))


def clear_screen() -> None:
    if rich_enabled():
        get_console().clear()
        return
    clear_screen_fallback()


def _readline_available() -> bool:
    try:
        import readline  # noqa: F401
    except Exception:
        return False
    return True


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _render_prompt_markup(prompt: str) -> str:
    from rich.console import Console
    from rich.text import Text

    base = get_console()
    temp = Console(
        force_terminal=True,
        color_system=base.color_system,
        legacy_windows=False,
        highlight=False,
        soft_wrap=True,
        emoji=False,
        record=True,
        file=io.StringIO(),
    )
    temp.print(Text.from_markup(prompt), end="")
    ansi = temp.export_text(styles=True)
    if ansi.endswith("\n"):
        ansi = ansi[:-1]
    if _readline_available():
        ansi = _ANSI_RE.sub(lambda m: f"\001{m.group(0)}\002", ansi)
    return ansi


def prompt_input(prompt: str) -> str:
    """
    Read input from user.

    Always uses builtin `input()` to preserve line editing behavior.
    """
    return input(prompt)


def prompt_input_markup(prompt: str) -> str:
    """
    Read input from user with Rich markup in the prompt.
    """
    if not rich_enabled():
        try:
            from rich.text import Text

            return input(Text.from_markup(prompt).plain)
        except Exception:
            return input(prompt)
    return input(_render_prompt_markup(prompt))


def print_panel(title: str, body: str) -> None:
    if rich_enabled():
        from rich.panel import Panel
        from rich.text import Text

        console = get_console()
        console.print(
            Panel(
                Text(body),
                title=title,
                border_style="bright_black",
                title_align="left",
            )
        )
        return
    print(panel(title, body, width=term_width(), pstyle=PanelStyle()))


@contextmanager
def status(message: str, *, spinner: str = "dots") -> Iterator[None]:
    """A transient spinner/status line while doing blocking work."""
    if not rich_enabled():
        yield
        return
    console = get_console()
    with console.status(message, spinner=spinner):
        yield


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[: max(0, max_chars - 1)] + "…", True


def print_tool_output(name: str, output: str, max_preview: int = 800) -> None:
    """Print formatted tool output."""
    if name in {"Task"}:
        return

    if name == "Skill":
        if rich_enabled():
            get_console().print(f"[bright_black]Skill loaded ({len(output)} chars)[/]")
            return
        print(style(f"  Skill loaded ({len(output)} chars)", fg="bright_black"))
        return

    full = os.environ.get("MINIBOT_FULL_TOOL_OUTPUT", "").strip().lower() in {"1", "true", "yes", "on"}
    original = output or ""
    text = original
    if not full:
        text, truncated = _truncate(text, max_preview)
        if truncated:
            text += "\n\n(set MINIBOT_FULL_TOOL_OUTPUT=1 to disable truncation)"

    if rich_enabled():
        from rich.panel import Panel
        from rich.pretty import Pretty
        from rich.text import Text

        renderable = None
        stripped = original.strip()
        if stripped and stripped[0] in "[{":
            try:
                if len(original) <= 200_000:
                    obj = json.loads(original)
                    renderable = Pretty(obj, expand_all=False)
            except Exception:
                renderable = None

        if renderable is None:
            renderable = Text(text)

        get_console().print(
            Panel(
                renderable,
                title=f"Tool Output: {name}",
                border_style="bright_black",
                title_align="left",
            )
        )
        return

    print(panel(f"Tool Output: {name}", text, width=term_width(), pstyle=PanelStyle()))
