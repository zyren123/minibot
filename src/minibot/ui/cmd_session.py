"""/session command handler."""

from __future__ import annotations

from typing import Any, TypedDict

from ..session.manager import SessionManager
from ..utils.output import print_panel, print_system


class SessionAction(TypedDict, total=False):
    """Action for the REPL to execute after /session command."""

    switch_to: str  # session_id to switch to
    new_history: list[dict]  # history to load


async def handle_session_cmd(
    raw_args: str,
    session_manager: SessionManager | None,
    current_session_id: str,
) -> SessionAction | None:
    """Dispatch /session subcommands. Returns an action for the REPL, or None."""
    if session_manager is None:
        print_system("Session persistence is disabled.")
        return None

    parts = raw_args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "list":
        _list_sessions(session_manager, current_session_id)
        return None
    if sub == "new":
        return _new_session(session_manager)
    if sub == "load":
        return await _load_session(session_manager, rest, current_session_id)
    if sub == "delete":
        return _delete_session(session_manager, rest, current_session_id)
    if sub == "info":
        _info_session(session_manager, current_session_id)
        return None

    _print_usage()
    return None


def _list_sessions(manager: SessionManager, current_id: str) -> None:
    sessions = manager.list_all()
    if not sessions:
        print_system("No saved sessions.")
        return

    try:
        from rich.table import Table
        from ..utils.rich_utils import get_console

        table = Table(title="Sessions", show_header=True, header_style="bold bright_cyan")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Created", style="green", no_wrap=True)
        table.add_column("Modified", style="yellow", no_wrap=True)
        table.add_column("Msgs", style="white", no_wrap=True, justify="right")
        table.add_column("Preview", style="dim")
        for s in sessions:
            id_display = f"▸ {s.session_id}" if s.session_id == current_id else f"  {s.session_id}"
            table.add_row(
                id_display,
                s.created_at.strftime("%m-%d %H:%M"),
                s.modified_at.strftime("%m-%d %H:%M"),
                str(s.message_count),
                s.preview,
            )
        get_console().print(table)
    except ImportError:
        lines: list[str] = []
        for s in sessions:
            marker = "▸" if s.session_id == current_id else " "
            lines.append(
                f"{marker} {s.session_id}  {s.modified_at:%m-%d %H:%M}  "
                f"msgs={s.message_count}  {s.preview}"
            )
        print_panel("Sessions", "\n".join(lines))


def _new_session(manager: SessionManager) -> SessionAction:
    new_id = manager.create()
    print_system(f"Created new session: {new_id}")
    return {"switch_to": new_id, "new_history": []}


async def _load_session(
    manager: SessionManager,
    session_id: str,
    current_id: str,
) -> SessionAction | None:
    if not session_id:
        return await _interactive_load(manager, current_id)

    if not manager.exists(session_id):
        print_system(f"Session '{session_id}' not found.")
        return None

    history = manager.load(session_id)
    print_system(f"Loaded session {session_id} ({len(history)} messages).")
    return {"switch_to": session_id, "new_history": history}


async def _interactive_load(
    manager: SessionManager,
    current_id: str,
) -> SessionAction | None:
    sessions = manager.list_all()
    if not sessions:
        print_system("No saved sessions.")
        return None

    from ..ui.interactive import interactive_choice

    def _render(meta: Any, idx: int, cursor: int) -> str:
        marker = "▸" if idx == cursor else " "
        current = " (current)" if meta.session_id == current_id else ""
        return (
            f"{marker} {meta.session_id}{current}  "
            f"{meta.modified_at:%m-%d %H:%M}  msgs={meta.message_count}  "
            f"{meta.preview}"
        )

    chosen = await interactive_choice(sessions, render_fn=_render)
    if chosen is None:
        return None

    history = manager.load(chosen.session_id)
    print_system(f"Loaded session {chosen.session_id} ({len(history)} messages).")
    return {"switch_to": chosen.session_id, "new_history": history}


def _delete_session(
    manager: SessionManager,
    session_id: str,
    current_id: str,
) -> SessionAction | None:
    if not session_id:
        print_system("Usage: /session delete <id>")
        return None
    if session_id == current_id:
        print_system("Cannot delete the current active session.")
        return None
    if not manager.delete(session_id):
        print_system(f"Session '{session_id}' not found.")
        return None
    print_system(f"Session '{session_id}' deleted.")
    return None


def _info_session(manager: SessionManager, current_id: str) -> None:
    sessions = manager.list_all()
    meta = next((s for s in sessions if s.session_id == current_id), None)
    if meta is None:
        print_system(f"Current session: {current_id} (not yet persisted)")
        return
    info = (
        f"Session ID:  {meta.session_id}\n"
        f"Created:     {meta.created_at:%Y-%m-%d %H:%M:%S}\n"
        f"Modified:    {meta.modified_at:%Y-%m-%d %H:%M:%S}\n"
        f"Messages:    {meta.message_count}\n"
        f"Preview:     {meta.preview}\n"
        f"Path:        {meta.path}"
    )
    print_panel("Session Info", info)


def _print_usage() -> None:
    usage = (
        "/session list              List all sessions\n"
        "/session new               Create a new session\n"
        "/session load [id]         Load a session (interactive if no id)\n"
        "/session delete <id>       Delete a session\n"
        "/session info              Show current session info"
    )
    print_panel("Session Commands", usage)
