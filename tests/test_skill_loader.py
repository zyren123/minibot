from pathlib import Path

import pytest

from src.minibot.skills.loader import SkillLoader


def _write_skill(root: Path, folder: str, name: str, description: str, body: str = "Body.") -> None:
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_loader_includes_builtin_skills_by_default(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path / "missing-skills")

    names = set(loader.list_skills())
    assert "skill-installer" in names
    assert "skill-creator" in names


def test_explicit_skill_dir_overrides_builtin_skill(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "custom-skill-creator", "skill-creator", "Custom creator", "Custom body.")

    loader = SkillLoader(skills_root)

    assert loader.skills["skill-creator"]["description"] == "Custom creator"
    assert loader.get_skill_content("skill-creator") == "# Skill: skill-creator\n\nCustom body."


def test_disabled_skills_hide_builtin_entries(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path / "missing-skills", disabled_skills=["skill-installer"])

    names = set(loader.list_skills())
    assert "skill-installer" not in names
    assert "skill-creator" in names


def test_loader_reads_skill_markdown_as_utf8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "utf8-skill", "utf8-skill", "Snowman: 雪人", "Body with emoji: 😀")

    original_read_text = Path.read_text
    encodings: list[str | None] = []

    def tracking_read_text(self: Path, *args, **kwargs):
        if self.name == "SKILL.md":
            encodings.append(kwargs.get("encoding"))
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)

    loader = SkillLoader(skills_root)

    assert loader.skills["utf8-skill"]["description"] == "Snowman: 雪人"
    assert encodings
    assert all(encoding == "utf-8" for encoding in encodings)
