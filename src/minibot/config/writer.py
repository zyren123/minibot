"""Config writer — persist runtime changes back to YAML files.

Supports source-aware write-back: detects whether a config item originates
from the global (~/.minibot/config/) or project (project/config/) file and
writes back to the correct one.
"""

from pathlib import Path
from typing import Any

import yaml


def _load_raw(path: Path) -> dict:
    """Load YAML preserving all top-level structure."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_raw(path: Path, data: dict) -> None:
    """Write dict back to YAML, preserving readable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def save_subagents_config(
    config_path: Path,
    agents: dict[str, dict[str, Any]],
) -> None:
    """Write subagents section to the config file."""
    data = _load_raw(config_path)
    data["subagents"] = agents
    _save_raw(config_path, data)


def save_mcp_server_enabled(
    config_path: Path,
    server_name: str,
    enabled: bool,
) -> None:
    """Toggle ``enabled`` for a single MCP server in the config file."""
    data = _load_raw(config_path)
    servers = data.get("servers", [])
    for server in servers:
        if isinstance(server, dict) and server.get("name") == server_name:
            server["enabled"] = enabled
            break
    data["servers"] = servers
    _save_raw(config_path, data)
