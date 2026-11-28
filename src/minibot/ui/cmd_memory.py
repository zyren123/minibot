"""/memory command handler."""

from __future__ import annotations

import json

from ..memory.manager import MemoryManager
from ..memory.remote_novelmemo import RemoteNovelMemoUnavailable, RemoteNovelMemoWorkspace
from ..memory.workspace import FileMemoryWorkspace, NovelMemoWorkspace, call_memory_operation
from ..utils.output import print_panel, print_system


def _parse_args(raw: str) -> tuple[str, str]:
    """Split 'subcommand rest' from raw args string."""
    parts = raw.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    return sub.lower(), rest.strip()


async def handle_memory_cmd(
    raw_args: str,
    memory_manager: MemoryManager | FileMemoryWorkspace | NovelMemoWorkspace | RemoteNovelMemoWorkspace | None,
) -> None:
    """Dispatch /memory against the Agent's active memory source."""
    if memory_manager is None:
        print_system("Memory is disabled.")
        return

    sub, rest = _parse_args(raw_args)
    try:
        if isinstance(memory_manager, FileMemoryWorkspace):
            _handle_file_memory(sub, rest, memory_manager)
        else:
            await _handle_uri_memory(sub, rest, memory_manager)
    except (ValueError, OSError, RemoteNovelMemoUnavailable) as exc:
        print_system(str(exc))


def _handle_file_memory(sub: str, rest: str, workspace: FileMemoryWorkspace) -> None:
    if sub == "boot":
        print_panel("File Memory", workspace.get_context_for_prompt() or "No memory notes yet.")
    elif sub == "index":
        paths = [entry["id"] for entry in workspace.directory("daily")["entries"]]
        print_panel("Memory Files", "\n".join(["LONG_TERM.md", *paths]))
    elif sub == "read" and rest:
        document = workspace.document(rest)
        print_panel(document["filename"], document["content"] or "This note is empty.")
    elif sub == "search" and rest:
        print_panel("Memory Search", json.dumps(workspace.search(rest), ensure_ascii=False, indent=2))
    else:
        print_panel("Memory Commands", (
            "/memory boot               Show long-term and recent daily notes\n"
            "/memory index              List memory files\n"
            "/memory read <path>        Read LONG_TERM.md or daily/YYYY-MM-DD.md\n"
            "/memory search <query>     Search memory files"
        ))


async def _handle_uri_memory(
    sub: str, rest: str, workspace: MemoryManager | NovelMemoWorkspace | RemoteNovelMemoWorkspace,
) -> None:
    novelmemo = isinstance(workspace, (NovelMemoWorkspace, RemoteNovelMemoWorkspace))
    read = workspace.read_memory if novelmemo else workspace.read
    if sub == "boot":
        print_panel("Memory Boot", await call_memory_operation(read, "system://boot"))
    elif sub == "index":
        if novelmemo and not rest:
            print_system("Usage: /memory index <domain>")
            return
        index_uri = f"system://index/{rest}" if novelmemo else "system://index"
        print_panel("Memory Index", await call_memory_operation(read, index_uri))
    elif sub == "glossary":
        print_panel("Memory Glossary", await call_memory_operation(read, "system://glossary"))
    elif sub == "read":
        if not rest:
            print_system("Usage: /memory read <uri>")
            return
        print_panel(f"Memory {rest}", await call_memory_operation(read, rest))
    elif sub == "search":
        if not rest:
            print_system("Usage: /memory search <query>")
            return
        payload = await call_memory_operation(workspace.search_memory, rest) if novelmemo else [
            {
                "uri": item.uri,
                "title": item.title,
                "kind": item.kind,
                "node_type": item.node_type,
                "matched_by": item.matched_by,
            }
            for item in workspace.search(rest)
        ]
        print_panel("Memory Search", json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        index_help = "index <domain>      Show a Novel Memo domain" if novelmemo else "index              Show system://index"
        print_panel("Memory Commands", (
            "/memory boot               Show system://boot\n"
            f"/memory {index_help}\n"
            "/memory glossary           Show system://glossary\n"
            "/memory read <uri>         Read one memory URI\n"
            "/memory search <query>     Search active memory"
        ))
