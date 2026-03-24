"""/team command handler."""

from __future__ import annotations

from typing import Any

from ..server.bots import BotStore, DEFAULT_BOT_ID
from ..utils.output import print_panel, print_system


async def handle_team_cmd(
    raw_args: str,
    agent: Any,
    *,
    global_enabled: bool,
) -> None:
    sub = raw_args.strip().split()[0].lower() if raw_args.strip() else "status"
    store = BotStore(app_home=agent.config.app_home)
    current = bool(store.read_bot_json(DEFAULT_BOT_ID).get("teams_enabled", True))

    if sub == "status":
        effective = bool(global_enabled) and current
        print_system(
            f"Team tools: configured={'on' if current else 'off'} "
            f"effective={'on' if effective else 'off'}"
        )
        return

    if sub not in {"on", "off"}:
        _print_usage()
        return

    requested = sub == "on"
    store.patch_bot_json(DEFAULT_BOT_ID, {"teams_enabled": requested})
    effective = bool(global_enabled) and requested
    await agent.set_team_tools_enabled(enabled=effective)
    if requested and not global_enabled:
        print_system("Team tools were enabled for the default bot, but global config still disables them in this session.")
        return
    print_system(f"Team tools {'enabled' if effective else 'disabled'}.")


def _print_usage() -> None:
    print_panel("Team Commands", "/team status    Show current team-tools state\n/team on        Enable team tools\n/team off       Disable team tools")
