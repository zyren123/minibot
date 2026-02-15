"""Configuration loading and management."""

import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from .schema import (
    Config,
    LLMConfig,
    ToolsConfig,
    HooksConfig,
    MCPConfig,
    MCPServerConfig,
    HookDefinition,
    MemoryConfig,
    TeamsConfig,
)
from ..utils.path import resolve_app_home, resolve_project_root

_config: Config | None = None


_DEFAULT_CONFIG_TEMPLATE = """# Minibot Default Configuration

skills_dir: skills

llm:
  base_url: ${OPENAI_BASE_URL}
  api_key: ${OPENAI_API_KEY}
  model: ${MODEL_ID:gpt-4.1-mini}
  max_tokens: 8000
  stream_enabled: true

tools:
  enabled:
    - "*"
  disabled: []
  timeout: 60

memory:
  enabled: true
  memory_dir: memory
  long_term_max_lines: 200
  daily_lookback_days: 1

teams:
  enabled: true
  max_members: 6
  default_members: 3
  log_dir: .minibot/teams
  wait_timeout_sec: 30
  quiet_teammates: true
  debug_teammate_output: false
"""


_HOOKS_CONFIG_TEMPLATE = """# Minibot Hooks Configuration

enabled: true
hooks_dir: hooks
hooks: []
"""


_MCP_CONFIG_TEMPLATE = """# Minibot MCP Servers Configuration

enabled: true
servers: []
"""


def _resolve_env_vars(value: Any, env_defaults: dict[str, str] | None = None) -> Any:
    """Resolve environment variable references in config values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        default = None
        if ":" in env_var:
            env_var, default = env_var.split(":", 1)
        resolved = os.getenv(env_var)
        if resolved is None and env_defaults is not None:
            resolved = env_defaults.get(env_var)
        return resolved if resolved is not None else default
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v, env_defaults) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v, env_defaults) for v in value]
    return value


def _load_yaml(path: Path, env_defaults: dict[str, str] | None = None) -> dict:
    """Load a YAML file and resolve environment variables."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return _resolve_env_vars(data, env_defaults)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge two dictionaries with override precedence."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_path_value(value: str | Path, base_dir: Path) -> Path:
    """Resolve a path value against a base directory."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _resolve_config_path(
    *,
    key: str,
    value: Any,
    base_dir: Path,
) -> Path:
    """Resolve a configured path, rejecting unset placeholders."""
    if value is None:
        raise ValueError(
            f"Configuration value '{key}' resolved to None. "
            "Set the referenced environment variable or provide a concrete path."
        )
    if not isinstance(value, (str, Path)):
        raise TypeError(
            f"Configuration value '{key}' must be a string or path, got {type(value).__name__}."
        )
    return _resolve_path_value(value, base_dir)


def _load_env_files(app_home: Path, project_root: Path) -> dict[str, str]:
    """Load .env defaults in order: global -> project."""
    merged: dict[str, str] = {}
    for path in (app_home / ".env", project_root / ".env"):
        if not path.exists():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                merged[key] = value
    return merged


def _bootstrap_global_config(
    *,
    app_home: Path,
    project_config_dir: Path,
) -> None:
    """Create global config files when missing, using project files as seed."""
    global_config_dir = (app_home / "config").resolve()
    try:
        global_config_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only app home should not block loading project-local config.
        return

    files: list[tuple[str, str]] = [
        ("default.yaml", _DEFAULT_CONFIG_TEMPLATE),
        ("hooks.yaml", _HOOKS_CONFIG_TEMPLATE),
        ("mcp_servers.yaml", _MCP_CONFIG_TEMPLATE),
    ]

    for filename, template in files:
        global_file = global_config_dir / filename
        if global_file.exists():
            continue
        project_file = project_config_dir / filename
        try:
            if project_file.exists():
                shutil.copy2(project_file, global_file)
                continue
            global_file.write_text(template.rstrip() + "\n", encoding="utf-8")
        except OSError:
            # Best-effort bootstrap: continue loading with whatever config is readable.
            continue


def _pick_path_value(
    *,
    key: str,
    global_data: dict,
    project_data: dict,
    global_base_dir: Path,
    project_base_dir: Path,
    default_base_dir: Path,
    default: str,
) -> tuple[Any, Path]:
    """Pick path config value with source-aware base dir."""
    if key in project_data:
        return project_data[key], project_base_dir
    if key in global_data:
        return global_data[key], global_base_dir
    return default, default_base_dir


def _parse_llm_config(data: dict, env_defaults: dict[str, str]) -> LLMConfig:
    """Parse LLM configuration."""
    return LLMConfig(
        base_url=(
            data.get("base_url")
            or os.getenv("OPENAI_BASE_URL")
            or env_defaults.get("OPENAI_BASE_URL")
        ),
        api_key=(
            data.get("api_key")
            or os.getenv("OPENAI_API_KEY")
            or env_defaults.get("OPENAI_API_KEY")
        ),
        model=(
            data.get("model")
            or os.getenv("MODEL_ID")
            or env_defaults.get("MODEL_ID")
            or "gpt-4.1-mini"
        ),
        max_tokens=data.get("max_tokens", 8000),
        stream_enabled=data.get("stream_enabled", True),
    )


def _parse_hooks_config(data: dict | None, hooks_dir: Path) -> HooksConfig:
    """Parse hooks configuration."""
    if data is None:
        data = {}
    hooks = []
    for hook_data in data.get("hooks") or []:
        hooks.append(HookDefinition(
            event=hook_data["event"],
            handler=hook_data["handler"],
            timeout=hook_data.get("timeout", 30),
            enabled=hook_data.get("enabled", True),
        ))

    return HooksConfig(
        enabled=data.get("enabled", True),
        hooks_dir=hooks_dir,
        hooks=hooks,
    )


def _parse_mcp_config(data: dict | None) -> MCPConfig:
    """Parse MCP configuration."""
    if data is None:
        data = {}
    servers = []
    for server_data in data.get("servers") or []:
        servers.append(MCPServerConfig(
            name=server_data["name"],
            transport=server_data.get("transport", "stdio"),
            command=server_data.get("command"),
            args=server_data.get("args", []),
            env=server_data.get("env", {}),
            url=server_data.get("url"),
            enabled=server_data.get("enabled", True),
        ))

    return MCPConfig(
        enabled=data.get("enabled", True),
        servers=servers,
    )


def _parse_memory_config(data: dict | None, app_home: Path) -> MemoryConfig:
    """Parse memory configuration."""
    if data is None:
        data = {}
    raw_memory_dir = data.get("memory_dir", "memory")
    resolved_memory_dir = _resolve_config_path(
        key="memory.memory_dir",
        value=raw_memory_dir,
        base_dir=app_home,
    )
    return MemoryConfig(
        enabled=data.get("enabled", True),
        memory_dir=str(resolved_memory_dir),
        long_term_max_lines=data.get("long_term_max_lines", 200),
        daily_lookback_days=data.get("daily_lookback_days", 1),
    )


def _parse_teams_config(data: dict | None) -> TeamsConfig:
    """Parse teams configuration."""
    if data is None:
        data = {}
    return TeamsConfig(
        enabled=data.get("enabled", True),
        max_members=data.get("max_members", 6),
        default_members=data.get("default_members", 3),
        log_dir=data.get("log_dir", ".minibot/teams"),
        wait_timeout_sec=data.get("wait_timeout_sec", 30),
        quiet_teammates=data.get("quiet_teammates", True),
        debug_teammate_output=data.get("debug_teammate_output", False),
    )


def load_config(
    config_dir: Path | None = None,
    workdir: Path | None = None,
    app_home: Path | None = None,
    project_root: Path | None = None,
) -> Config:
    """Load configuration from files and environment variables."""
    global _config

    workdir = (workdir or Path.cwd()).resolve()
    app_home = (app_home or resolve_app_home()).resolve()
    project_root = (project_root or resolve_project_root(workdir)).resolve()
    global_config_dir = (app_home / "config").resolve()
    project_config_dir = (config_dir or project_root / "config").resolve()

    _bootstrap_global_config(app_home=app_home, project_config_dir=project_config_dir)

    env_defaults = _load_env_files(app_home=app_home, project_root=project_root)

    global_default = _load_yaml(global_config_dir / "default.yaml", env_defaults)
    project_default = _load_yaml(project_config_dir / "default.yaml", env_defaults)
    global_hooks = _load_yaml(global_config_dir / "hooks.yaml", env_defaults)
    project_hooks = _load_yaml(project_config_dir / "hooks.yaml", env_defaults)
    global_mcp = _load_yaml(global_config_dir / "mcp_servers.yaml", env_defaults)
    project_mcp = _load_yaml(project_config_dir / "mcp_servers.yaml", env_defaults)

    default_config = _deep_merge(global_default, project_default)
    hooks_config = _deep_merge(global_hooks, project_hooks)
    mcp_config = _deep_merge(global_mcp, project_mcp)

    raw_skills_dir, skills_base_dir = _pick_path_value(
        key="skills_dir",
        global_data=global_default,
        project_data=project_default,
        global_base_dir=global_config_dir,
        project_base_dir=project_config_dir,
        default_base_dir=project_root,
        default="skills",
    )
    skills_dir = _resolve_config_path(
        key="skills_dir",
        value=raw_skills_dir,
        base_dir=skills_base_dir,
    )

    raw_hooks_dir, hooks_base_dir = _pick_path_value(
        key="hooks_dir",
        global_data=global_hooks,
        project_data=project_hooks,
        global_base_dir=global_config_dir,
        project_base_dir=project_config_dir,
        default_base_dir=project_root,
        default="hooks",
    )
    hooks_dir = _resolve_config_path(
        key="hooks_dir",
        value=raw_hooks_dir,
        base_dir=hooks_base_dir,
    )

    _config = Config(
        workdir=workdir,
        app_home=app_home,
        project_root=project_root,
        skills_dir=skills_dir,
        llm=_parse_llm_config(default_config.get("llm", {}), env_defaults),
        tools=ToolsConfig(
            enabled=default_config.get("tools", {}).get("enabled", ["*"]),
            disabled=default_config.get("tools", {}).get("disabled", []),
            timeout=default_config.get("tools", {}).get("timeout", 60),
        ),
        hooks=_parse_hooks_config(hooks_config, hooks_dir),
        mcp=_parse_mcp_config(mcp_config),
        memory=_parse_memory_config(default_config.get("memory"), app_home),
        teams=_parse_teams_config(default_config.get("teams")),
    )

    return _config


def get_config() -> Config:
    """Get the current configuration, loading if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
