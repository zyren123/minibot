"""Tests for subagent skills configuration."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessageCustomToolCall, ChatCompletionMessageFunctionToolCall

from src.minibot.config.schema import SubagentConfig, SubagentsConfig
from src.minibot.config.settings import load_config
from src.minibot.subagents.registry import AgentRegistry
from src.minibot.tools.registry import ToolRegistry
from src.minibot.tools.base import BaseTool
from src.minibot.subagents.executor import SubagentExecutor
from src.minibot.ui.cmd_agent import handle_agent_cmd


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


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["tool_calls", "stop"])
async def test_subagent_projects_tool_calls_only_when_reached(finish_reason):
    executor = _build_executor(skills_enabled=False)
    observed = []

    class Read(_FakeTool):
        async def execute(self, **kwargs):
            observed.append("read_file")
            return "read complete"

    class Model:
        async def create_message_async(self, **kwargs):
            function = ChatCompletionMessageFunctionToolCall(
                id="call-read", type="function", function={"name": "read_file", "arguments": "{}"},
            )
            unsupported = ChatCompletionMessageCustomToolCall(
                id="call-custom", type="custom", custom={"name": "read_file", "input": "README.md"},
            )
            message = SimpleNamespace(content="Finished.", tool_calls=[function, unsupported])
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])

    executor.tool_registry.register(Read("read_file"))
    executor.client = Model()
    if finish_reason == "tool_calls":
        with pytest.raises(AttributeError, match="function"):
            await executor.execute("Read", "Read the workspace.", "explore")
        assert observed == ["read_file"]
    else:
        assert await executor.execute("Read", "Read the workspace.", "explore") == "Finished."
        assert observed == []


def test_get_tools_includes_skill_when_enabled():
    executor = _build_executor(skills_enabled=True)
    tools = executor._get_tools_for_agent("explore")
    names = {t["function"]["name"] for t in tools}
    assert "Skill" in names
    assert names == {"read_file", "Skill"}


def test_get_tools_excludes_skill_when_disabled():
    executor = _build_executor(skills_enabled=False)
    tools = executor._get_tools_for_agent("explore")
    names = {t["function"]["name"] for t in tools}
    assert names == {"read_file"}


@pytest.mark.parametrize("agent_type", ["explore", "plan"])
def test_builtin_read_only_tools_and_explicit_override_are_isolated(agent_type):
    executor = _build_executor(skills_enabled=False)
    override = SubagentsConfig(agents={
        agent_type: SubagentConfig(tools=["bash", "read_file", "Skill", "Task", "TeamTask"]),
    })
    executor.agent_registry.apply_config(override)
    executor.tool_registry.register(_FakeTool("Task"))
    executor.tool_registry.register(_FakeTool("TeamTask"))

    overridden = executor._get_tools_for_agent(agent_type)
    assert {tool["function"]["name"] for tool in overridden} == {"bash", "read_file"}

    fresh = _build_executor(skills_enabled=False)
    defaults = fresh._get_tools_for_agent(agent_type)
    assert {tool["function"]["name"] for tool in defaults} == {"read_file"}


def test_custom_subagent_honors_empty_tool_list():
    executor = _build_executor(skills_enabled=False)
    executor.agent_registry.apply_config(SubagentsConfig(agents={
        "no_tools": SubagentConfig(description="Respond without tools", tools=[], prompt="Answer directly."),
    }))

    assert executor._get_tools_for_agent("no_tools") == []


@pytest.mark.asyncio
async def test_cli_toggle_preserves_builtin_tool_overrides(tmp_path):
    app_home = tmp_path / "app"
    project = tmp_path / "project"
    project.mkdir()
    path = app_home / "config" / "default.yaml"
    _write(path, """\
subagents:
  explore:
    tools: [bash, read_file]
  plan:
    tools: []
""")

    def read_registry():
        registry = AgentRegistry()
        config = load_config(workdir=project, app_home=app_home, project_root=project)
        registry.apply_config(config.subagents)
        return registry

    async def unexpected_prompt(*_args):
        pytest.fail("Toggling a subagent must not prompt")

    await handle_agent_cmd("disable code", read_registry(), path, unexpected_prompt)

    reloaded = read_registry()
    assert reloaded.get("explore").tools == ["bash", "read_file"]
    assert reloaded.get("plan").tools == []
    assert not reloaded.get("code").enabled


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
