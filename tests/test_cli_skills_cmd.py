from pathlib import Path

import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import Config, LLMConfig, MemoryConfig, SessionConfig, TeamsConfig
from src.minibot.ui.cmd_skills import load_skills_disabled, save_skills_disabled


def _write_skill(root: Path, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )


def test_bot_json_skills_disabled_roundtrip(tmp_path: Path) -> None:
    app_home = tmp_path / ".minibot"
    app_home.mkdir(parents=True, exist_ok=True)
    (app_home / "bot.json").write_text('{"llm": {"model": "x"}}\n', encoding="utf-8")

    save_skills_disabled(app_home, ["b", "a", "a", ""])
    assert load_skills_disabled(app_home) == ["a", "b"]

    # Ensure unrelated keys are preserved.
    text = (app_home / "bot.json").read_text(encoding="utf-8")
    assert '"llm"' in text

    save_skills_disabled(app_home, [])
    assert load_skills_disabled(app_home) == []


def test_agent_set_disabled_skills_refreshes_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")

    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "a-skill", "A")
    _write_skill(skills_root, "b-skill", "B")

    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        skills_dir=skills_root,
        llm=LLMConfig(base_url="https://example.invalid/v1", api_key="test", model="test-model"),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=False),
    )

    agent = Agent(cfg, disabled_skills=["a-skill"])
    assert "a-skill" not in agent.skill_loader.list_skills()
    assert "b-skill" in agent.skill_loader.list_skills()

    agent.set_disabled_skills([])
    assert "a-skill" in agent.skill_loader.list_skills()
