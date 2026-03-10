"""/mcp command handler — interactive server toggling."""

from __future__ import annotations

from pathlib import Path

from ..config.schema import MCPServerConfig
from ..config.writer import save_mcp_server_enabled
from ..mcp.manager import MCPManager
from ..utils.output import print_panel, print_system
from .interactive import interactive_select


async def handle_mcp_cmd(
    raw_args: str,
    mcp_manager: MCPManager,
    config_path: Path,
    tool_registry: "Any" = None,
) -> None:
    """Dispatch /mcp subcommands."""
    sub = raw_args.strip().split()[0].lower() if raw_args.strip() else ""

    if sub == "list" or sub == "":
        await _interactive_list(mcp_manager, config_path, tool_registry)
    else:
        _print_usage()


def _render_server(
    cfg: MCPServerConfig,
    index: int,
    cursor: int,
    mcp_manager: MCPManager,
) -> str:
    """Render a single server line for the interactive selector."""
    connected = mcp_manager.is_connected(cfg.name)
    enabled_mark = "●" if cfg.enabled else "○"
    conn_status = " [connected]" if connected else ""
    arrow = "▸ " if index == cursor else "  "
    return f"{arrow}{enabled_mark} {cfg.name} ({cfg.transport}){conn_status}"


async def _interactive_list(
    mcp_manager: MCPManager,
    config_path: Path,
    tool_registry: "Any",
) -> None:
    """Show interactive arrow-key selector for MCP servers."""
    configs = mcp_manager.get_all_server_configs()
    if not configs:
        print_system("No MCP servers configured.")
        return

    async def _on_toggle(cfg: MCPServerConfig) -> None:
        if mcp_manager.is_connected(cfg.name):
            await mcp_manager.disconnect_server(cfg.name)
            cfg.enabled = False
            save_mcp_server_enabled(config_path, cfg.name, False)
        else:
            cfg.enabled = True
            save_mcp_server_enabled(config_path, cfg.name, True)
            err = await mcp_manager.connect_server(cfg.name)
            if err is not None:
                cfg.enabled = False
                save_mcp_server_enabled(config_path, cfg.name, False)

    def _render(item: MCPServerConfig, idx: int, cur: int) -> str:
        return _render_server(item, idx, cur, mcp_manager)

    await interactive_select(configs, render_fn=_render, on_toggle=_on_toggle)


def _print_usage() -> None:
    usage = "/mcp list    Interactive MCP server selector (↑↓ navigate, Enter toggle)"
    print_panel("MCP Commands", usage)
