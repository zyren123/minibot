"""Path utilities."""

import os
from pathlib import Path


def safe_path(workdir: Path, path: str) -> Path:
    """Ensure path stays within workspace."""
    resolved = (workdir / path).resolve()
    if not resolved.is_relative_to(workdir):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def resolve_project_root(cwd: Path) -> Path:
    """Resolve project root by searching for a Git root from cwd upward."""
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def resolve_app_home() -> Path:
    """Resolve persistent minibot home directory."""
    home = os.getenv("MINIBOT_HOME", "").strip()
    if home:
        return Path(home).expanduser().resolve()

    xdg_config_home = os.getenv("XDG_CONFIG_HOME", "").strip()
    if xdg_config_home:
        return (Path(xdg_config_home).expanduser() / "minibot").resolve()

    return (Path.home() / ".minibot").resolve()
