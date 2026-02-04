"""Path utilities."""

from pathlib import Path


def safe_path(workdir: Path, path: str) -> Path:
    """Ensure path stays within workspace."""
    resolved = (workdir / path).resolve()
    if not resolved.is_relative_to(workdir):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved
