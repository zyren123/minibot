"""Skill loader for loading SKILL.md files."""

import re
from pathlib import Path
from typing import Any
from collections.abc import Iterable


class SkillLoader:
    """
    Loads and manages skills from SKILL.md files.

    A skill is a FOLDER containing:
    - SKILL.md (required): YAML frontmatter + markdown instructions
    - scripts/ (optional): Helper scripts
    - references/ (optional): Additional documentation
    - assets/ (optional): Templates, files for output
    """

    COMMON_SKILL_DIRS = (
        Path("~/.claude/skills"),
    )

    def __init__(
        self,
        skills_dir: Path | Iterable[Path],
        *,
        disabled_skills: Iterable[str] | None = None,
    ):
        self.skills_dirs = self._normalize_dirs(skills_dir)
        self.disabled_skills = {str(name) for name in (disabled_skills or [])}
        self.skills: dict[str, dict[str, Any]] = {}
        self.load_skills()

    def _normalize_dirs(self, skills_dir: Path | Iterable[Path]) -> list[Path]:
        if isinstance(skills_dir, (str, Path)):
            dirs = [Path(skills_dir)]
        else:
            dirs = [Path(path) for path in skills_dir]

        # Add common skill directories (if not already present).
        for common_dir in self.COMMON_SKILL_DIRS:
            dirs.append(common_dir)

        normalized: list[Path] = []
        for path in dirs:
            expanded = path.expanduser()
            if expanded not in normalized:
                normalized.append(expanded)

        return normalized

    def parse_skill_md(self, path: Path) -> dict | None:
        """Parse a SKILL.md file into metadata and body."""
        content = path.read_text()

        # Match YAML frontmatter between --- markers
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            return None

        frontmatter, body = match.groups()

        # Parse YAML-like frontmatter (simple key: value)
        metadata = {}
        for line in frontmatter.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")

        # Require name and description
        if "name" not in metadata or "description" not in metadata:
            return None

        return {
            "name": metadata["name"],
            "description": metadata["description"],
            "body": body.strip(),
            "path": path,
            "dir": path.parent,
        }

    def load_skills(self) -> None:
        """Scan skills directories and load all valid SKILL.md files."""
        for skills_dir in self.skills_dirs:
            if not skills_dir.exists():
                continue

            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue

                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                skill = self.parse_skill_md(skill_md)
                if not skill:
                    continue
                if skill["name"] in self.disabled_skills:
                    continue
                if skill["name"] not in self.skills:
                    self.skills[skill["name"]] = skill

    def get_descriptions(self) -> str:
        """Generate skill descriptions for system prompt."""
        if not self.skills:
            return "(no skills available)"

        return "\n".join(
            f"- {name}: {skill['description']}"
            for name, skill in self.skills.items()
        )

    def get_skill_content(self, name: str) -> str | None:
        """Get full skill content for injection."""
        if name not in self.skills:
            return None

        skill = self.skills[name]
        content = f"# Skill: {skill['name']}\n\n{skill['body']}"

        # List available resources
        resources = []
        for folder, label in [
            ("scripts", "Scripts"),
            ("references", "References"),
            ("assets", "Assets"),
        ]:
            folder_path = skill["dir"] / folder
            if folder_path.exists():
                files = list(folder_path.glob("*"))
                if files:
                    resources.append(f"{label}: {', '.join(f.name for f in files)}")

        if resources:
            content += f"\n\n**Available resources in {skill['dir']}:**\n"
            content += "\n".join(f"- {r}" for r in resources)

        return content

    def list_skills(self) -> list[str]:
        """Return list of available skill names."""
        return list(self.skills.keys())
