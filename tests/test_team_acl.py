import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import Config, LLMConfig
from src.minibot.teams.runtime import TeamRuntime


class FakeWorkerAgent:
    async def run_loop(self, messages):
        messages.append({"role": "assistant", "content": "ok"})
        return messages


@pytest.mark.asyncio
async def test_team_runtime_acl_create_team(tmp_path):
    cfg = Config(
        workdir=tmp_path,
        llm=LLMConfig(base_url="http://localhost:8000", api_key="test", model="test-model"),
    )
    runtime = TeamRuntime(config=cfg.teams, workdir=tmp_path)
    runtime.set_agent_factory(lambda _team_id, _member_id: FakeWorkerAgent())

    created = await runtime.create_team(actor_role="lead", team_name="x", member_count=1)
    assert created["name"] == "x"

    with pytest.raises(PermissionError):
        await runtime.create_team(actor_role="teammate", team_name="y", member_count=1)


def test_teammate_tool_acl(tmp_path):
    cfg = Config(
        workdir=tmp_path,
        llm=LLMConfig(base_url="http://localhost:8000", api_key="test", model="test-model"),
    )
    runtime = TeamRuntime(config=cfg.teams, workdir=tmp_path)
    runtime.set_agent_factory(lambda _team_id, _member_id: FakeWorkerAgent())

    teammate = Agent(
        config=cfg,
        role="teammate",
        team_runtime=runtime,
        team_id="team-1",
        member_id="member-1",
    )
    lead = Agent(config=cfg, role="lead", team_runtime=runtime)

    teammate_tools = {tool.name for tool in teammate.tool_registry.get_all()}
    lead_tools = {tool.name for tool in lead.tool_registry.get_all()}

    assert "Task" not in teammate_tools
    assert "TeamCreate" not in teammate_tools
    assert "TeamShutdown" not in teammate_tools
    assert "TeamCreate" in lead_tools
