#!/usr/bin/env python3
"""List installable skills from a GitHub repo path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error

from github_utils import github_api_contents_url, github_request, resolve_skills_dir

DEFAULT_REPO = "openai/skills"
DEFAULT_PATH = "skills/.curated"
DEFAULT_REF = "main"


class ListError(Exception):
    """Raised when a remote skill listing cannot be fetched or parsed."""


class Args(argparse.Namespace):
    repo: str
    path: str
    ref: str
    format: str
    installed_dir: str | None


def _request(url: str) -> bytes:
    return github_request(url, "minibot-skill-list")


def _installed_skills(installed_dir: str | None = None) -> set[str]:
    root = resolve_skills_dir(installed_dir)
    if not root.is_dir():
        return set()
    return {entry.name for entry in root.iterdir() if entry.is_dir()}


def _list_skills(repo: str, path: str, ref: str) -> list[str]:
    api_url = github_api_contents_url(repo, path, ref)
    try:
        payload = _request(api_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ListError(
                "Skills path not found: "
                f"https://github.com/{repo}/tree/{ref}/{path}"
            ) from exc
        raise ListError(f"Failed to fetch skills: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ListError(f"Failed to fetch skills: {exc.reason}") from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ListError("Unexpected skills listing response.") from exc

    if not isinstance(data, list):
        raise ListError("Unexpected skills listing response.")
    skills = [item["name"] for item in data if item.get("type") == "dir" and isinstance(item.get("name"), str)]
    return sorted(skills)


def _parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="List installable skills.")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="Repo path to list (default: skills/.curated)",
    )
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--installed-dir",
        help="Existing local skills directory to annotate as already installed",
    )
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        skills = _list_skills(args.repo, args.path, args.ref)
        installed = _installed_skills(args.installed_dir)
        if args.format == "json":
            payload = [
                {"name": name, "installed": name in installed}
                for name in skills
            ]
            print(json.dumps(payload))
        else:
            for idx, name in enumerate(skills, start=1):
                suffix = " (already installed)" if name in installed else ""
                print(f"{idx}. {name}{suffix}")
        return 0
    except ListError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
