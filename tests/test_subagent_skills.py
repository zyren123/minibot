"""Tests for subagent skills configuration."""

from pathlib import Path

from src.minibot.config.schema import SubagentConfig, SubagentsConfig
from src.minibot.config.settings import load_config
from src.minibot.subagents.registry import AgentRegistry
from src.minibot.tools.registry import ToolRegistry
from src.minibot.tools.base import BaseTool
from src.minibot.subagents.executor import SubagentExecutor


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- AgentRegistry.apply_config ---


def test_apply_config_enables_skills():
    registry = AgentRegistry()
    config = SubagentsConfig(agents={
        "code": SubagentConfig(skills_enabled=True),
    })
    registry.apply_config(config)

    agent = registry.get("code")
    assert agent is not None
    assert agent.skills_enabled is True

    # Unaffected agents remain default
    explore = registry.get("explore")
    assert explore is not None
    assert explore.skills_enabled is False


def test_apply_config_overrides_description_and_prompt():
    registry = AgentRegistry()
    config = SubagentsConfig(agents={
        "plan": SubagentConfig(
            description="Custom planner",
            prompt="Plan carefully.",
            skills_enabled=True,
        ),
    })
    registry.apply_config(config)

    agent = registry.get("plan")
    assert agent is not None
    assert agent.description == "Custom planner"
    assert agent.prompt == "Plan carefully."
    assert agent.skills_enabled is True


def test_apply_config_ignores_unknown_agent():
    registry = AgentRegistry()
    config = SubagentsConfig(agents={
        "nonexistent": SubagentConfig(skills_enabled=True),
    })
    # Should not raise
    registry.apply_config(config)
    assert registry.get("nonexistent") is None


# --- _get_tools_for_agent with skills ---


class _FakeTool(BaseTool):
    """Minimal tool stub for testing."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return ""

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        return ""


def _build_executor(skills_enabled: bool) -> SubagentExecutor:
    """Build an executor with explore agent and configurable skills_enabled."""
    registry = AgentRegistry()
    config = SubagentsConfig(agents={
        "explore": SubagentConfig(skills_enabled=skills_enabled),
    })
    registry.apply_config(config)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("bash"))
    tool_registry.register(_FakeTool("read_file"))
    tool_registry.register(_FakeTool("Skill"))

    return SubagentExecutor(
        client=None,  # type: ignore[arg-type]
        tool_registry=tool_registry,
        agent_registry=registry,
        hook_manager=None,  # type: ignore[arg-type]
        workdir=Path("/tmp"),
    )


def test_get_tools_includes_skill_when_enabled():
    executor = _build_executor(skills_enabled=True)
    tools = executor._get_tools_for_agent("explore")
    names = {t["function"]["name"] for t in tools}
    assert "Skill" in names
    assert "bash" in names
    assert "read_file" in names


def test_get_tools_excludes_skill_when_disabled():
    executor = _build_executor(skills_enabled=False)
    tools = executor._get_tools_for_agent("explore")
    names = {t["function"]["name"] for t in tools}
    assert "Skill" not in names
    assert "bash" in names
    assert "read_file" in names


def test_get_tools_wildcard_agent_with_skills():
    registry = AgentRegistry()
    config = SubagentsConfig(agents={
        "code": SubagentConfig(skills_enabled=True),
    })
    registry.apply_config(config)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("bash"))
    tool_registry.register(_FakeTool("Skill"))
    tool_registry.register(_FakeTool("Task"))

    executor = SubagentExecutor(
        client=None,  # type: ignore[arg-type]
        tool_registry=tool_registry,
        agent_registry=registry,
        hook_manager=None,  # type: ignore[arg-type]
        workdir=Path("/tmp"),
    )

    tools = executor._get_tools_for_agent("code")
    names = {t["function"]["name"] for t in tools}
    assert "Skill" in names
    assert "bash" in names
    # Task is always excluded for subagents
    assert "Task" not in names


# --- Config parsing ---


def test_load_config_parses_subagents_skills(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(
        app_home / "config" / "default.yaml",
        """\
subagents:
  explore:
    skills_enabled: true
  code:
    skills_enabled: false
""",
    )

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_ID", raising=False)

    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert "explore" in config.subagents.agents
    assert config.subagents.agents["explore"].skills_enabled is True
    assert "code" in config.subagents.agents
    assert config.subagents.agents["code"].skills_enabled is False
