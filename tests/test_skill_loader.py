from pathlib import Path

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
