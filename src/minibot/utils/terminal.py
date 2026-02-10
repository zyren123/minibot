"""Terminal rendering helpers (stdlib-only)."""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
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

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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
        columns = shutil.get_terminal_size((default, 24)).columns
    except Exception:
        columns = default
    # Avoid printing into the last column to prevent terminal auto-wrap artifacts.
    return max(1, columns - 1)


def _char_width(ch: str) -> int:
    if ch == "\0":
        return 0
    code = ord(ch)
    if code < 32 or code == 127:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in {"Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1


def _cell_len(text: str) -> int:
    plain = _ANSI_RE.sub("", text)
    return sum(_char_width(ch) for ch in plain)


def _iter_ansi(text: str) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    last = 0
    for match in _ANSI_RE.finditer(text):
        if match.start() > last:
            parts.append((text[last : match.start()], False))
        parts.append((match.group(0), True))
        last = match.end()
    if last < len(text):
        parts.append((text[last:], False))
    return parts


def _wrap_line(text: str, width: int) -> list[str]:
    if text == "":
        return [""]
    if width <= 0:
        return [text]
    lines: list[str] = []
    current = ""
    current_w = 0
    for seg, is_ansi in _iter_ansi(text):
        if is_ansi:
            current += seg
            continue
        for ch in seg:
            if ch == "\t":
                tab = 4 - (current_w % 4)
                if current_w + tab > width and current_w > 0:
                    lines.append(current)
                    current = ""
                    current_w = 0
                current += " " * tab
                current_w += tab
                continue
            ch_w = _char_width(ch)
            if current_w + ch_w > width and current_w > 0:
                lines.append(current)
                current = ""
                current_w = 0
            current += ch
            current_w += ch_w
    lines.append(current)
    return lines


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
    term = term_width()
    w = min(width or term, 140, term)
    min_w = 10 if term >= 10 else term
    w = max(min_w, w)
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
        wrapped = _wrap_line(raw_line, inner_w)
        for seg in wrapped:
            lines.append(seg)

    out_lines = [style(top, fg=pstyle.border_fg)]
    for line in lines:
        padded = line + (" " * max(0, inner_w - _cell_len(line)))
        out_lines.append(style("│", fg=pstyle.border_fg) + f" {padded} " + style("│", fg=pstyle.border_fg))
    out_lines.append(style(bottom, fg=pstyle.border_fg))
    return "\n".join(out_lines)
