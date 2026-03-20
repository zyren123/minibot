#!/usr/bin/env python3
"""Quick structural validation for Minibot skills."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_FRONTMATTER_KEYS = {"name", "description", "metadata", "license", "allowed-tools"}
RESOURCE_DIRS = {"scripts", "references", "assets"}


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    """Validate the minimal structure and frontmatter for a skill directory."""
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Could not read SKILL.md: {exc}"

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not match:
        return False, "Invalid or missing YAML frontmatter"

    frontmatter_text, body = match.groups()
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"

    if not isinstance(frontmatter, dict):
        return False, "Frontmatter must be a YAML object"

    unexpected_keys = sorted(set(frontmatter) - ALLOWED_FRONTMATTER_KEYS)
    if unexpected_keys:
        return False, f"Unexpected frontmatter keys: {', '.join(unexpected_keys)}"

    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "Missing or invalid 'name'"
    name = name.strip()
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return False, "Skill name must be lowercase hyphen-case"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, "Skill name cannot start/end with hyphen or contain consecutive hyphens"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters"

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        return False, "Missing or invalid 'description'"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets"
    if len(description.strip()) > 1024:
        return False, "Description exceeds 1024 characters"

    if not body.strip():
        return False, "SKILL.md body is empty"

    for resource in RESOURCE_DIRS:
        resource_path = skill_dir / resource
        if resource_path.exists() and not resource_path.is_dir():
            return False, f"{resource} exists but is not a directory"

    return True, "Skill is valid"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one or more Minibot skills.")
    parser.add_argument("skill_dirs", nargs="+", help="Skill directories to validate")
    args = parser.parse_args(argv)

    all_valid = True
    for raw_path in args.skill_dirs:
        valid, message = validate_skill(raw_path)
        prefix = "OK" if valid else "ERROR"
        print(f"[{prefix}] {raw_path}: {message}")
        all_valid = all_valid and valid
    return 0 if all_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
