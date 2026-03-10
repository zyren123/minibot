"""/memory command handler."""

from __future__ import annotations

from ..memory.manager import MemoryManager
from ..utils.output import print_panel, print_system
from .interactive import edit_in_vim


def _parse_args(raw: str) -> tuple[str, str]:
    """Split 'subcommand rest' from raw args string."""
    parts = raw.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    return sub.lower(), rest.strip()


async def handle_memory_cmd(raw_args: str, memory_manager: MemoryManager) -> None:
    """Dispatch /memory subcommands."""
    if memory_manager is None:
        print_system("Memory is disabled.")
        return

    sub, rest = _parse_args(raw_args)

    if sub == "show":
        _show_long_term(memory_manager)
    elif sub == "list":
        _list_daily(memory_manager)
    elif sub == "edit":
        _edit_long_term(memory_manager)
    elif sub == "append":
        _append_long_term(rest, memory_manager)
    elif sub == "daily":
        _handle_daily(rest, memory_manager)
    else:
        _print_usage()


def _show_long_term(mm: MemoryManager) -> None:
    content = mm.read_long_term()
    if not content:
        print_system("Long-term memory is empty.")
        return
    print_panel("Long-term Memory", content)


def _list_daily(mm: MemoryManager) -> None:
    dates = mm.list_daily_files()
    if not dates:
        print_system("No daily memory files.")
        return
    print_panel("Daily Memory Files", "\n".join(dates))


def _edit_long_term(mm: MemoryManager) -> None:
    current = mm.read_long_term()
    result = edit_in_vim(current)
    if result is None:
        print_system("Edit cancelled.")
        return
    msg = mm.write_long_term(result)
    print_system(msg)


def _append_long_term(text: str, mm: MemoryManager) -> None:
    if not text:
        print_system("Usage: /memory append <text>")
        return
    msg = mm.append_long_term(text)
    print_system(msg)


def _handle_daily(rest: str, mm: MemoryManager) -> None:
    """Handle /memory daily [edit] [date]."""
    parts = rest.strip().split(maxsplit=1)
    if not parts:
        # /memory daily → show today
        _show_daily(None, mm)
        return

    if parts[0].lower() == "edit":
        date = parts[1] if len(parts) > 1 else None
        _edit_daily(date, mm)
    else:
        _show_daily(parts[0], mm)


def _show_daily(date: str | None, mm: MemoryManager) -> None:
    content = mm.read_daily(date)
    label = date or "today"
    if not content:
        print_system(f"Daily memory ({label}) is empty.")
        return
    print_panel(f"Daily Memory ({label})", content)


def _edit_daily(date: str | None, mm: MemoryManager) -> None:
    current = mm.read_daily(date)
    result = edit_in_vim(current)
    if result is None:
        print_system("Edit cancelled.")
        return
    msg = mm.write_daily(result, date)
    print_system(msg)


def _print_usage() -> None:
    usage = (
        "/memory show           Show long-term memory\n"
        "/memory edit           Edit long-term memory (vim)\n"
        "/memory append <text>  Append to long-term memory\n"
        "/memory list           List daily memory dates\n"
        "/memory daily [date]   Show daily memory\n"
        "/memory daily edit [date]  Edit daily memory (vim)"
    )
    print_panel("Memory Commands", usage)
