"""/skills command handler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent import Agent
from ..skills.loader import SkillLoader
from ..utils.output import print_panel, print_system


def load_skills_disabled(app_home: Path) -> list[str]:
    data = _read_bot_json(app_home)
    raw = data.get("skills_disabled") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    out.sort()
    return out


def save_skills_disabled(app_home: Path, disabled: list[str]) -> None:
    data = _read_bot_json(app_home)
    normalized = sorted({str(s).strip() for s in disabled if str(s).strip()})
    if normalized:
        data["skills_disabled"] = normalized
    else:
        data.pop("skills_disabled", None)
    _write_bot_json(app_home, data)


def _read_bot_json(app_home: Path) -> dict[str, Any]:
    path = app_home / "bot.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_bot_json(app_home: Path, data: dict[str, Any]) -> None:
    app_home.mkdir(parents=True, exist_ok=True)
    path = app_home / "bot.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class SkillItem:
    name: str
    description: str


async def handle_skills_cmd(raw_args: str, agent: Agent) -> None:
    """Dispatch /skills subcommands."""
    parts = raw_args.strip().split(maxsplit=1) if raw_args.strip() else []
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in {"", "toggle"}:
        if rest:
            _toggle_one(agent, rest)
            return
        await _interactive_toggle(agent)
        return
    if sub == "list":
        _list_skills(agent)
        return
    if sub == "enable":
        if not rest:
            _print_usage()
            return
        _enable_one(agent, rest)
        return
    if sub == "disable":
        if not rest:
            _print_usage()
            return
        _disable_one(agent, rest)
        return

    _print_usage()


def _all_skills(agent: Agent) -> list[SkillItem]:
    loader = SkillLoader(agent.skill_loader.skills_dirs)
    items = [
        SkillItem(name=name, description=str(meta.get("description") or ""))
        for name, meta in loader.skills.items()
    ]
    items.sort(key=lambda s: s.name)
    return items


def _current_disabled(agent: Agent) -> set[str]:
    return set(load_skills_disabled(agent.config.app_home))


def _apply_disabled(agent: Agent, disabled: set[str]) -> None:
    ordered = sorted(disabled)
    save_skills_disabled(agent.config.app_home, ordered)
    agent.set_disabled_skills(ordered)


def _list_skills(agent: Agent) -> None:
    items = _all_skills(agent)
    if not items:
        print_system("No skills found.")
        return

    disabled = _current_disabled(agent)

    try:
        from rich.table import Table
        from ..utils.rich_utils import get_console

        table = Table(title="Skills", show_header=True, header_style="bold bright_cyan")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Enabled", style="white", no_wrap=True)
        table.add_column("Description", style="dim")
        for s in items:
            enabled = s.name not in disabled
            table.add_row(s.name, "on" if enabled else "off", s.description)
        get_console().print(table)
    except ImportError:
        lines: list[str] = []
        for s in items:
            enabled = s.name not in disabled
            lines.append(f"{'[on] ' if enabled else '[off]'} {s.name}  {s.description}")
        print_panel("Skills", "\n".join(lines))

    print_system("Tip: /skills (interactive)  /skills enable <name>  /skills disable <name>")


def _enable_one(agent: Agent, name: str) -> None:
    items = {s.name for s in _all_skills(agent)}
    if name not in items:
        print_system(f"Unknown skill '{name}'. Try /skills list.")
        return
    disabled = _current_disabled(agent)
    if name not in disabled:
        print_system(f"Skill '{name}' is already enabled.")
        return
    disabled.remove(name)
    _apply_disabled(agent, disabled)
    print_system(f"Enabled skill: {name}")


def _disable_one(agent: Agent, name: str) -> None:
    items = {s.name for s in _all_skills(agent)}
    if name not in items:
        print_system(f"Unknown skill '{name}'. Try /skills list.")
        return
    disabled = _current_disabled(agent)
    if name in disabled:
        print_system(f"Skill '{name}' is already disabled.")
        return
    disabled.add(name)
    _apply_disabled(agent, disabled)
    print_system(f"Disabled skill: {name}")


def _toggle_one(agent: Agent, name: str) -> None:
    items = {s.name for s in _all_skills(agent)}
    if name not in items:
        print_system(f"Unknown skill '{name}'. Try /skills list.")
        return
    disabled = _current_disabled(agent)
    if name in disabled:
        disabled.remove(name)
        _apply_disabled(agent, disabled)
        print_system(f"Enabled skill: {name}")
        return
    disabled.add(name)
    _apply_disabled(agent, disabled)
    print_system(f"Disabled skill: {name}")


async def _interactive_toggle(agent: Agent) -> None:
    items = _all_skills(agent)
    if not items:
        print_system("No skills found.")
        return

    disabled = _current_disabled(agent)

    try:
        from ..ui.interactive import interactive_select
    except Exception:
        _list_skills(agent)
        return

    def _render(item: SkillItem, idx: int, cursor: int) -> str:
        marker = "▸" if idx == cursor else " "
        state = "on " if item.name not in disabled else "off"
        desc = f" - {item.description}" if item.description else ""
        return f"{marker} [{state}] {item.name}{desc}"

    def _on_toggle(item: SkillItem) -> None:
        if item.name in disabled:
            disabled.remove(item.name)
        else:
            disabled.add(item.name)
        _apply_disabled(agent, disabled)

    await interactive_select(items, render_fn=_render, on_toggle=_on_toggle)
    print_system("Skills updated.")


def _print_usage() -> None:
    usage = (
        "/skills                 Interactive toggle UI\n"
        "/skills list            List skills\n"
        "/skills enable <name>   Enable a skill\n"
        "/skills disable <name>  Disable a skill\n"
        "/skills toggle <name>   Toggle a skill"
    )
    print_panel("Skills Commands", usage)

