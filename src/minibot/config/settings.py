"""Configuration loading and management."""

import os
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    Config,
    LLMConfig,
    ToolsConfig,
    HooksConfig,
    MCPConfig,
    MCPServerConfig,
    HookDefinition,
)

_config: Config | None = None


def _resolve_env_vars(value: Any) -> Any:
    """Resolve environment variable references in config values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        default = None
        if ":" in env_var:
            env_var, default = env_var.split(":", 1)
        return os.getenv(env_var, default)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and resolve environment variables."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return _resolve_env_vars(data)


def _parse_llm_config(data: dict) -> LLMConfig:
    """Parse LLM configuration."""
    return LLMConfig(
        base_url=data.get("base_url") or os.getenv("OPENAI_BASE_URL"),
        api_key=data.get("api_key") or os.getenv("OPENAI_API_KEY"),
        model=data.get("model") or os.getenv("MODEL_ID", "gpt-4.1-mini"),
        max_tokens=data.get("max_tokens", 8000),
    )


def _parse_hooks_config(data: dict | None, workdir: Path) -> HooksConfig:
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

    hooks_dir = data.get("hooks_dir", "hooks")
    if not Path(hooks_dir).is_absolute():
        hooks_dir = workdir / hooks_dir

    return HooksConfig(
        enabled=data.get("enabled", True),
        hooks_dir=Path(hooks_dir),
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


def load_config(
    config_dir: Path | None = None,
    workdir: Path | None = None,
) -> Config:
    """Load configuration from files and environment variables."""
    global _config

    workdir = workdir or Path.cwd()
    config_dir = config_dir or workdir / "config"

    # Load config files
    default_config = _load_yaml(config_dir / "default.yaml")
    hooks_config = _load_yaml(config_dir / "hooks.yaml")
    mcp_config = _load_yaml(config_dir / "mcp_servers.yaml")

    # Merge configurations
    skills_dir = default_config.get("skills_dir", "skills")
    skills_dir = Path(skills_dir).expanduser()
    if not skills_dir.is_absolute():
        skills_dir = workdir / skills_dir

    _config = Config(
        workdir=workdir,
        skills_dir=Path(skills_dir),
        llm=_parse_llm_config(default_config.get("llm", {})),
        tools=ToolsConfig(
            enabled=default_config.get("tools", {}).get("enabled", ["*"]),
            disabled=default_config.get("tools", {}).get("disabled", []),
            timeout=default_config.get("tools", {}).get("timeout", 60),
        ),
        hooks=_parse_hooks_config(hooks_config, workdir),
        mcp=_parse_mcp_config(mcp_config),
    )

    return _config


def get_config() -> Config:
    """Get the current configuration, loading if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
