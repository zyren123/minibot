#!/usr/bin/env python3
"""Initialize a new Minibot skill directory from a template."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    from minibot.config import load_config
except ImportError:  # pragma: no cover - repo tests import via src.minibot
    from src.minibot.config import load_config

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}

SKILL_TEMPLATE = """---
name: {skill_name}
description: [TODO: Describe what this skill does and when it should be used.]
---

# {skill_title}

## Goal

[TODO: Explain the outcome this skill enables.]

## Workflow

1. [TODO: Describe the first important step.]
2. [TODO: Describe the second important step.]
3. [TODO: Describe how to verify the result.]

## Notes

- Keep this file concise.
- Move long docs into `references/` when needed.
- Add helper scripts only when deterministic execution matters.
"""

EXAMPLE_SCRIPT = '''#!/usr/bin/env python3
"""Example helper for {skill_name}."""


def main() -> None:
    print("Replace this example with real logic.")


if __name__ == "__main__":
    main()
'''

EXAMPLE_REFERENCE = """# {skill_title} Reference

Move detailed domain knowledge here when SKILL.md would otherwise become too large.
"""

EXAMPLE_ASSET = """This placeholder represents an output asset or template file."""


def normalize_skill_name(skill_name: str) -> str:
    """Normalize a skill name to lowercase hyphen-case."""
    normalized = skill_name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def title_case_skill_name(skill_name: str) -> str:
    """Convert hyphenated skill name to Title Case for display."""
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def parse_resources(raw_resources: str) -> list[str]:
    if not raw_resources:
        return []
    resources = [item.strip() for item in raw_resources.split(",") if item.strip()]
    invalid = sorted({item for item in resources if item not in ALLOWED_RESOURCES})
    if invalid:
        allowed = ", ".join(sorted(ALLOWED_RESOURCES))
        raise ValueError(f"Unknown resource type(s): {', '.join(invalid)}. Allowed: {allowed}")
    deduped: list[str] = []
    seen: set[str] = set()
    for resource in resources:
        if resource not in seen:
            deduped.append(resource)
            seen.add(resource)
    return deduped


def create_resource_dirs(
    skill_dir: Path,
    skill_name: str,
    skill_title: str,
    resources: list[str],
    include_examples: bool,
) -> None:
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(exist_ok=True)
        if resource == "scripts" and include_examples:
            (resource_dir / "example.py").write_text(
                EXAMPLE_SCRIPT.format(skill_name=skill_name),
                encoding="utf-8",
            )
        elif resource == "references" and include_examples:
            (resource_dir / "reference.md").write_text(
                EXAMPLE_REFERENCE.format(skill_title=skill_title),
                encoding="utf-8",
            )
        elif resource == "assets" and include_examples:
            (resource_dir / "example_asset.txt").write_text(EXAMPLE_ASSET, encoding="utf-8")


def init_skill(
    skill_name: str,
    path: str | Path,
    resources: list[str],
    include_examples: bool,
) -> Path:
    """Initialize a new skill directory with a starter layout."""
    skill_dir = Path(path).resolve() / skill_name
    if skill_dir.exists():
        raise FileExistsError(f"Skill directory already exists: {skill_dir}")

    skill_dir.mkdir(parents=True, exist_ok=False)
    skill_title = title_case_skill_name(skill_name)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        SKILL_TEMPLATE.format(skill_name=skill_name, skill_title=skill_title),
        encoding="utf-8",
    )

    if resources:
        create_resource_dirs(skill_dir, skill_name, skill_title, resources, include_examples)

    return skill_dir


def resolve_default_output_dir() -> Path:
    try:
        return Path(load_config().skills_dir).resolve()
    except Exception:
        home = os.environ.get("MINIBOT_HOME", "").strip()
        if home:
            return (Path(home).expanduser() / "skills").resolve()
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if xdg_config_home:
            return (Path(xdg_config_home).expanduser() / "minibot" / "skills").resolve()
        return (Path.home() / ".minibot" / "skills").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a new Minibot skill directory with a SKILL.md template.",
    )
    parser.add_argument("skill_name", help="Skill name (normalized to hyphen-case)")
    parser.add_argument(
        "--path",
        help="Output directory for the skill (defaults to Minibot's configured user skills_dir)",
    )
    parser.add_argument(
        "--resources",
        default="",
        help="Comma-separated list: scripts,references,assets",
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        help="Create example files inside the selected resource directories",
    )
    args = parser.parse_args(argv)

    raw_skill_name = args.skill_name
    skill_name = normalize_skill_name(raw_skill_name)
    if not skill_name:
        print("Error: skill name must include at least one letter or digit.", file=sys.stderr)
        return 1
    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(
            f"Error: skill name '{skill_name}' is too long ({len(skill_name)} characters). "
            f"Maximum is {MAX_SKILL_NAME_LENGTH} characters.",
            file=sys.stderr,
        )
        return 1

    try:
        resources = parse_resources(args.resources)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.examples and not resources:
        print("Error: --examples requires --resources to be set.", file=sys.stderr)
        return 1

    if skill_name != raw_skill_name:
        print(f"Normalized skill name from '{raw_skill_name}' to '{skill_name}'.")

    target_path = args.path or str(resolve_default_output_dir())
    try:
        result = init_skill(skill_name, target_path, resources, args.examples)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Initialized skill at {result}")
    print("Next steps:")
    print("1. Replace the TODOs in SKILL.md")
    print("2. Add or edit resource files only where needed")
    print(f"3. Run quick_validate.py {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
