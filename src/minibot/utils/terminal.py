"""Terminal rendering helpers (stdlib-only)."""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from dataclasses import dataclass


ANSI_RESET = "\x1b[0m"

FG = {
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "bright_black": "90",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_blue": "94",
    "bright_magenta": "95",
    "bright_cyan": "96",
    "bright_white": "97",
}


def is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def supports_ansi() -> bool:
    if not is_tty():
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    term = os.environ.get("TERM", "")
    return term not in ("", "dumb")


def style(text: str, *, fg: str | None = None, bold: bool = False, dim: bool = False) -> str:
    if not supports_ansi():
        return text
    codes: list[str] = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if fg:
        codes.append(FG.get(fg, fg))
    if not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}{ANSI_RESET}"


def clear_screen() -> None:
    if not is_tty():
        return
    # ANSI clear + home
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def term_width(default: int = 100) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


@dataclass(frozen=True)
class PanelStyle:
    title_fg: str = "bright_cyan"
    border_fg: str = "bright_black"


def panel(title: str, body: str, *, width: int | None = None, pstyle: PanelStyle = PanelStyle()) -> str:
    """
    Render a unicode box around text.

    Notes:
    - Keeps existing line breaks.
    - Wraps long lines to fit the box.
    """
    w = max(40, min(width or term_width(), 140))
    inner_w = w - 4  # padding + borders
    title_txt = f" {title.strip()} " if title.strip() else ""

    top_rule = "─" * max(1, w - 2)
    if title_txt:
        # Insert title into top rule: ── title ──
        available = w - 2
        trimmed = title_txt[: max(0, available - 2)]
        left = max(1, (available - len(trimmed)) // 2)
        right = max(1, available - len(trimmed) - left)
        top_rule = ("─" * left) + trimmed + ("─" * right)

    top = f"╭{top_rule}╮"
    bottom = f"╰{'─' * (w - 2)}╯"

    lines: list[str] = []
    for raw_line in (body or "").splitlines() or [""]:
        wrapped = textwrap.wrap(raw_line, width=inner_w, replace_whitespace=False, drop_whitespace=False)
        if not wrapped:
            wrapped = [""]
        for seg in wrapped:
            lines.append(seg)

    out_lines = [style(top, fg=pstyle.border_fg)]
    for line in lines:
        padded = line + (" " * max(0, inner_w - len(line)))
        out_lines.append(style("│", fg=pstyle.border_fg) + f" {padded} " + style("│", fg=pstyle.border_fg))
    out_lines.append(style(bottom, fg=pstyle.border_fg))
    return "\n".join(out_lines)

