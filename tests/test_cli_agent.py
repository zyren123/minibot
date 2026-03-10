"""Tests for /agent command handler and AgentRegistry extensions."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.minibot.subagents.registry import AgentRegistry, AgentType


# --- AgentRegistry ---


def test_enable_disable_agent():
    registry = AgentRegistry()
    assert registry.get("explore").enabled is True
    assert registry.disable("explore") is True
    assert registry.get("explore").enabled is False
    assert registry.enable("explore") is True
    assert registry.get("explore").enabled is True


def test_disable_returns_false_for_unknown():
    registry = AgentRegistry()
    assert registry.disable("nonexistent") is False


def test_unregister_blocks_default_agents():
    registry = AgentRegistry()
    assert registry.unregister("explore") is False
    assert registry.get("explore") is not None


def test_unregister_removes_custom_agent():
    registry = AgentRegistry()
    registry.register(AgentType(
        name="custom", description="test", tools=["bash"], prompt="do stuff",
    ))
    assert registry.get("custom") is not None
    assert registry.unregister("custom") is True
    assert registry.get("custom") is None


def test_list_all_returns_all_agents():
    registry = AgentRegistry()
    agents = registry.list_all()
    names = {a.name for a in agents}
    assert {"explore", "code", "plan"} == names


def test_to_config_dict_includes_all():
    registry = AgentRegistry()
    registry.register(AgentType(
        name="my_agent", description="desc", tools=["bash"], prompt="p",
    ))
    d = registry.to_config_dict()
    assert "explore" in d
    assert "my_agent" in d
    assert d["my_agent"]["description"] == "desc"
    assert d["my_agent"]["tools"] == ["bash"]


def test_get_descriptions_excludes_disabled():
    registry = AgentRegistry()
    registry.disable("plan")
    desc = registry.get_descriptions()
    assert "plan" not in desc
    assert "explore" in desc


# --- handle_agent_cmd ---


@pytest.mark.asyncio
async def test_agent_list_dispatches():
    from src.minibot.ui.cmd_agent import handle_agent_cmd
    registry = AgentRegistry()

    async def prompt_fn(a: str, b: str) -> str:
        return ""

    with patch("src.minibot.ui.cmd_agent._list_agents") as mock_list:
        await handle_agent_cmd("list", registry, Path("/tmp/g.yaml"), prompt_fn)
    mock_list.assert_called_once_with(registry)


@pytest.mark.asyncio
async def test_agent_enable_dispatches():
    from src.minibot.ui.cmd_agent import handle_agent_cmd
    registry = AgentRegistry()
    registry.disable("code")

    async def prompt_fn(a: str, b: str) -> str:
        return ""

    with patch("src.minibot.ui.cmd_agent._persist"):
        with patch("src.minibot.ui.cmd_agent.print_system"):
            await handle_agent_cmd("enable code", registry, Path("/tmp/g.yaml"), prompt_fn)

    assert registry.get("code").enabled is True
