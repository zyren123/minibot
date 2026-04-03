"""/memory command handler."""

from __future__ import annotations

import json

from ..memory.manager import MemoryManager
from ..utils.output import print_panel, print_system


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

    if sub == "boot":
        print_panel("Memory Boot", memory_manager.read("system://boot"))
    elif sub == "index":
        print_panel("Memory Index", memory_manager.read("system://index"))
    elif sub == "glossary":
        print_panel("Memory Glossary", memory_manager.read("system://glossary"))
    elif sub == "read":
        if not rest:
            print_system("Usage: /memory read <uri>")
            return
        print_panel(f"Memory {rest}", memory_manager.read(rest))
    elif sub == "search":
        if not rest:
            print_system("Usage: /memory search <query>")
            return
        results = memory_manager.search(rest)
        payload = [
            {
                "uri": item.uri,
                "title": item.title,
                "kind": item.kind,
                "node_type": item.node_type,
                "matched_by": item.matched_by,
            }
            for item in results
        ]
        print_panel("Memory Search", json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_usage()


def _print_usage() -> None:
    usage = (
        "/memory boot               Show system://boot\n"
        "/memory index              Show system://index\n"
        "/memory glossary           Show system://glossary\n"
        "/memory read <uri>         Read one memory URI\n"
        "/memory search <query>     Search current namespace"
    )
    print_panel("Memory Commands", usage)
