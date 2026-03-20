#!/usr/bin/env python3
"""Shared GitHub and Minibot path helpers for skill install scripts."""

from __future__ import annotations

import os
from pathlib import Path
import urllib.request

import yaml


def github_request(url: str, user_agent: str) -> bytes:
    headers = {"User-Agent": user_agent}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def github_api_contents_url(repo: str, path: str, ref: str) -> str:
    return f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"


def resolve_minibot_home() -> Path:
    home = os.environ.get("MINIBOT_HOME", "").strip()
    if home:
        return Path(home).expanduser().resolve()

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg_config_home:
        return (Path(xdg_config_home).expanduser() / "minibot").resolve()

    return (Path.home() / ".minibot").resolve()


def resolve_skills_dir(raw_path: str | None = None) -> Path:
    if raw_path:
        path = Path(raw_path).expanduser()
        return path.resolve() if path.is_absolute() else Path.cwd().joinpath(path).resolve()

    app_home = resolve_minibot_home()
    config_dir = (app_home / "config").resolve()
    default_dir = (app_home / "skills").resolve()
    config_path = config_dir / "default.yaml"
    if not config_path.exists():
        return default_dir

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return default_dir

    raw_skills_dir = data.get("skills_dir")
    if not isinstance(raw_skills_dir, str) or not raw_skills_dir.strip():
        return default_dir

    path = Path(raw_skills_dir).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (config_dir / path).resolve()
