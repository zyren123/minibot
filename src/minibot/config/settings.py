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
    SubagentConfig,
    SubagentsConfig,
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

subagents:
  explore:
    skills_enabled: false
  code:
    skills_enabled: false
  plan:
    skills_enabled: false
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


def _load_env_files(app_home: Path, project_root: Path | None = None) -> dict[str, str]:
    """Load .env defaults from global app home, creating from .env.example if missing."""
    merged: dict[str, str] = {}
    path = app_home / ".env"

    if not path.exists() and project_root:
        example_path = project_root / ".env.example"
        if example_path.exists():
            try:
                shutil.copy2(example_path, path)
                from ..utils.rich_utils import get_console, rich_enabled
                if rich_enabled():
                    console = get_console()
                    console.print(f"\n[bold yellow]⚠️  Created global .env file at {path}[/bold yellow]")
                    console.print("[bold yellow]⚠️  Please configure your MODEL_ID, OPENAI_API_KEY, and OPENAI_BASE_URL before continuing.[/bold yellow]\n")
                else:
                    print(f"\n⚠️  Created global .env file at {path}")
                    print("⚠️  Please configure your MODEL_ID, OPENAI_API_KEY, and OPENAI_BASE_URL before continuing.\n")
            except OSError:
                pass

    if path.exists():
        for key, value in dotenv_values(path).items():
            if value is not None:
                merged[key] = value
    return merged


def _bootstrap_global_config(app_home: Path, project_root: Path | None = None) -> None:
    """Create global config files when missing."""
    global_config_dir = (app_home / "config").resolve()
    try:
        global_config_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only app home
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
            
        legacy_file = project_root / "config" / filename if project_root else None
        
        try:
            if legacy_file and legacy_file.exists():
                shutil.copy2(legacy_file, global_file)
            else:
                global_file.write_text(template.rstrip() + "\n", encoding="utf-8")
        except OSError:
            # Best-effort bootstrap: continue loading with whatever config is readable.
            continue





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


def _parse_subagents_config(data: dict | None) -> SubagentsConfig:
    """Parse subagents configuration."""
    if data is None:
        return SubagentsConfig()
    agents: dict[str, SubagentConfig] = {}
    for name, agent_data in data.items():
        if not isinstance(agent_data, dict):
            continue
        agents[name] = SubagentConfig(
            description=agent_data.get("description"),
            tools=agent_data.get("tools"),
            prompt=agent_data.get("prompt"),
            skills_enabled=agent_data.get("skills_enabled", False),
            enabled=agent_data.get("enabled", True),
        )
    return SubagentsConfig(agents=agents)


def load_config(
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

    _bootstrap_global_config(app_home, project_root)

    env_defaults = _load_env_files(app_home, project_root)

    default_config = _load_yaml(global_config_dir / "default.yaml", env_defaults)
    hooks_config = _load_yaml(global_config_dir / "hooks.yaml", env_defaults)
    mcp_config = _load_yaml(global_config_dir / "mcp_servers.yaml", env_defaults)

    raw_skills_dir = default_config.get("skills_dir", "skills")
    skills_dir = _resolve_config_path(
        key="skills_dir",
        value=raw_skills_dir,
        base_dir=global_config_dir,
    )

    raw_hooks_dir = hooks_config.get("hooks_dir", "hooks")
    hooks_dir = _resolve_config_path(
        key="hooks_dir",
        value=raw_hooks_dir,
        base_dir=global_config_dir,
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
        subagents=_parse_subagents_config(default_config.get("subagents")),
    )

    return _config


def get_config() -> Config:
    """Get the current configuration, loading if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
