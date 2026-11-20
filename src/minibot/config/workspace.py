"""Host runtime policy for a restricted workspace."""

import os
from pathlib import Path
import sys

from .schema import Config


def require_outside_workspace(config: Config, path: Path, label: str) -> None:
    if config.workspace.restricted:
        require_outside_root(config.workdir, path, label)


def require_outside_root(root: Path, path: Path, label: str) -> None:
    root = root.resolve()
    absolute = path.absolute()
    resolved = absolute.resolve()
    if absolute.is_relative_to(root) or resolved.is_relative_to(root):
        raise ValueError(f"{label} must be outside the restricted Docker workspace")
    if not root.exists():
        return
    root_stat = root.stat()
    for ancestor in {*absolute.parents, *resolved.parents, absolute, resolved}:
        # Filesystem aliases, including case variants on macOS, may compare
        # differently as paths but still be exposed by the bind mount.
        if ancestor.exists() and os.path.samestat(ancestor.stat(), root_stat):
            raise ValueError(f"{label} must be outside the restricted Docker workspace")


def validate_workspace_runtime(config: Config) -> None:
    """Check runtime state paths before creating stores or connecting host tools."""
    if not config.workspace.restricted:
        return
    for label, path in (
        ("MiniBot installation", Path(__file__).resolve().parents[1]),
        ("Python environment", Path(sys.prefix)),
        ("Python executable", Path(sys.executable)),
    ):
        require_outside_workspace(config, path, label)
    home = config.app_home
    require_outside_workspace(config, home, "app_home")
    for name in (
        ".env", "config", "config/default.yaml", "config/hooks.yaml", "config/mcp_servers.yaml",
        "artifacts", "runs", "runs/journal.sqlite3", "history", "history/history", "history/prompt_history",
        "bots", "bot.json", "soul.md", "dashboard.json", "platforms.json", "platform_connections",
    ):
        require_outside_workspace(config, home / name, f"app_home/{name}")
    if config.session.enabled or config.durable_execution or config.context_offloading.enabled:
        require_outside_workspace(config, Path(config.session.sessions_dir), "session.sessions_dir")
    if config.trace_dir is not None:
        require_outside_workspace(config, Path(config.trace_dir), "trace_dir")
    if config.file_checkpoints:
        raise ValueError("Git file checkpoints require a trusted local workspace")
    if config.hooks.enabled and any(hook.enabled for hook in config.hooks.hooks):
        raise ValueError("Host hooks are unavailable in a restricted Docker workspace")
    if config.mcp.enabled and any(server.enabled for server in config.mcp.servers):
        raise ValueError("Host MCP servers are unavailable in a restricted Docker workspace")
