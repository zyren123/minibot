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


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
            continue
        target[key] = value
    return target


def update_default_config(config_path: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Patch global default config YAML and return saved data."""
    data = _load_raw(config_path)
    _deep_merge(data, patch)
    _save_raw(config_path, data)
    return data


def upsert_env_value(env_path: Path, key: str, value: str | None) -> None:
    """Upsert one dotenv key without clobbering unrelated lines."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    rendered = None if value is None else f"{key}={value}"
    updated: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            if rendered is not None:
                updated.append(rendered)
            replaced = True
        else:
            updated.append(line)

    if not replaced and rendered is not None:
        updated.append(rendered)

    output = "\n".join(updated).rstrip()
    if output:
        env_path.write_text(output + "\n", encoding="utf-8")
    elif env_path.exists():
        env_path.unlink()


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


def update_mcp_server_config(
    config_path: Path,
    server_name: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Update one MCP server block in the config file and return the saved mapping."""
    data = _load_raw(config_path)
    servers = data.get("servers", [])

    for server in servers:
        if not isinstance(server, dict) or server.get("name") != server_name:
            continue
        for key in ("command", "url"):
            if key in patch:
                server[key] = patch.get(key)
        if "args" in patch:
            server["args"] = list(patch.get("args") or [])
        if "enabled" in patch:
            server["enabled"] = bool(patch.get("enabled"))
        data["servers"] = servers
        _save_raw(config_path, data)
        return dict(server)

    raise ValueError(f"Unknown MCP server: {server_name}")
