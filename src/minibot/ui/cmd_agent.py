"""/agent command handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config.writer import save_subagents_config
from ..subagents.registry import AgentRegistry, AgentType
from ..utils.output import print_panel, print_system


PromptFn = Callable[[str, str], Awaitable[str]]


async def handle_agent_cmd(
    raw_args: str,
    registry: AgentRegistry,
    config_path: Path,
    prompt_fn: PromptFn,
) -> None:
    """Dispatch /agent subcommands.

    *prompt_fn(label_rich, label_plain)* → user input string.
    """
    parts = raw_args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "list":
        _list_agents(registry)
    elif sub == "info":
        _info_agent(rest, registry)
    elif sub == "enable":
        _toggle_agent(rest, registry, config_path, enabled=True)
    elif sub == "disable":
        _toggle_agent(rest, registry, config_path, enabled=False)
    elif sub == "create":
        await _create_agent(registry, config_path, prompt_fn)
    elif sub == "delete":
        _delete_agent(rest, registry, config_path)
    else:
        _print_usage()


def _list_agents(registry: AgentRegistry) -> None:
    agents = registry.list_all()
    if not agents:
        print_system("No agents registered.")
        return

    try:
        from rich.table import Table
        from ..utils.rich_utils import get_console

        table = Table(title="Subagents", show_header=True, header_style="bold bright_cyan")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Enabled", style="green", no_wrap=True)
        table.add_column("Skills", style="yellow", no_wrap=True)
        table.add_column("Tools", style="white")
        table.add_column("Description", style="dim")
        for a in agents:
            is_default = a.name in registry.DEFAULT_AGENT_NAMES
            tools_str = "*" if a.tools == "*" else ", ".join(a.tools)
            name_display = f"{a.name} (default)" if is_default else a.name
            table.add_row(
                name_display,
                "✓" if a.enabled else "✗",
                "✓" if a.skills_enabled else "✗",
                tools_str,
                a.description[:60],
            )
        get_console().print(table)
    except ImportError:
        lines = []
        for a in agents:
            status = "on" if a.enabled else "off"
            lines.append(f"  {a.name:<12} [{status}] {a.description[:50]}")
        print_panel("Subagents", "\n".join(lines))


def _info_agent(name: str, registry: AgentRegistry) -> None:
    if not name:
        print_system("Usage: /agent info <name>")
        return
    agent = registry.get(name)
    if agent is None:
        print_system(f"Agent '{name}' not found.")
        return
    tools_str = "*" if agent.tools == "*" else ", ".join(agent.tools)
    info = (
        f"Name:           {agent.name}\n"
        f"Enabled:        {agent.enabled}\n"
        f"Skills enabled: {agent.skills_enabled}\n"
        f"Tools:          {tools_str}\n"
        f"Default:        {agent.name in registry.DEFAULT_AGENT_NAMES}\n"
        f"Description:    {agent.description}\n"
        f"Prompt:         {agent.prompt}"
    )
    print_panel(f"Agent: {agent.name}", info)


def _toggle_agent(
    name: str,
    registry: AgentRegistry,
    config_path: Path,
    *,
    enabled: bool,
) -> None:
    if not name:
        action = "enable" if enabled else "disable"
        print_system(f"Usage: /agent {action} <name>")
        return
    fn = registry.enable if enabled else registry.disable
    if not fn(name):
        print_system(f"Agent '{name}' not found.")
        return
    _persist(registry, config_path)
    state = "enabled" if enabled else "disabled"
    print_system(f"Agent '{name}' {state}.")


async def _create_agent(
    registry: AgentRegistry,
    config_path: Path,
    prompt_fn: PromptFn,
) -> None:
    name = await prompt_fn("Name: ", "Name: ")
    if not name:
        print_system("Cancelled.")
        return
    if registry.get(name):
        print_system(f"Agent '{name}' already exists.")
        return

    description = await prompt_fn("Description: ", "Description: ")
    tools_raw = await prompt_fn("Tools (comma-separated, or * for all): ", "Tools: ")
    tools: list[str] | str = (
        "*" if tools_raw.strip() == "*"
        else [t.strip() for t in tools_raw.split(",") if t.strip()]
    )
    prompt = await prompt_fn("Prompt: ", "Prompt: ")

    agent = AgentType(
        name=name, description=description, tools=tools, prompt=prompt,
    )
    registry.register(agent)
    _persist(registry, config_path)
    print_system(f"Agent '{name}' created.")


def _delete_agent(name: str, registry: AgentRegistry, config_path: Path) -> None:
    if not name:
        print_system("Usage: /agent delete <name>")
        return
    if name in registry.DEFAULT_AGENT_NAMES:
        print_system(f"Cannot delete default agent '{name}'. Use /agent disable instead.")
        return
    if not registry.unregister(name):
        print_system(f"Agent '{name}' not found.")
        return
    _persist(registry, config_path)
    print_system(f"Agent '{name}' deleted.")


def _persist(registry: AgentRegistry, config_path: Path) -> None:
    save_subagents_config(config_path, registry.to_config_dict())


def _print_usage() -> None:
    usage = (
        "/agent list              List all agents\n"
        "/agent info <name>       Show agent details\n"
        "/agent enable <name>     Enable an agent\n"
        "/agent disable <name>    Disable an agent\n"
        "/agent create            Create a new agent\n"
        "/agent delete <name>     Delete a custom agent"
    )
    print_panel("Agent Commands", usage)
