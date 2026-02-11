import asyncio

import pytest

from src.minibot.config.schema import TeamsConfig
from src.minibot.teams.context import TeamExecutionContext, team_execution_context
from src.minibot.teams.runtime import TeamRuntime
from src.minibot.tools.builtin.file import WriteFileTool


class FakeWorkerAgent:
    async def run_loop(self, messages):
        await asyncio.sleep(0.05)
        messages.append({"role": "assistant", "content": "done"})
        return messages


class ClosableFakeWorkerAgent:
    def __init__(self):
        self.close_calls = 0

    async def run_loop(self, messages):
        await asyncio.sleep(0.01)
        messages.append({"role": "assistant", "content": "done"})
        return messages

    async def close_client(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_parallel_message_workers_and_wait(tmp_path):
    runtime = TeamRuntime(config=TeamsConfig(), workdir=tmp_path)
    runtime.set_agent_factory(lambda _team_id, _member_id: FakeWorkerAgent())
    await runtime.create_team(actor_role="lead", team_name="demo", member_count=2)

    await runtime.send_message(
        sender_id="lead",
        recipient_id="member-1",
        content="work on A",
    )
    await runtime.send_message(
        sender_id="lead",
        recipient_id="member-2",
        content="work on B",
    )

    idle_seen = set()
    for _ in range(6):
        events = await runtime.wait_events(timeout_sec=1, max_events=20)
        for event in events:
            if event.get("type") == "teammate_idle":
                idle_seen.add(event.get("member_id"))
        if idle_seen == {"member-1", "member-2"}:
            break

    assert idle_seen == {"member-1", "member-2"}
    await runtime.cleanup_team(actor_role="lead")


@pytest.mark.asyncio
async def test_file_lock_conflict_and_release(tmp_path):
    runtime = TeamRuntime(config=TeamsConfig(), workdir=tmp_path)
    runtime.set_agent_factory(lambda _team_id, _member_id: FakeWorkerAgent())
    created = await runtime.create_team(actor_role="lead", team_name="demo", member_count=2)
    team_id = created["team_id"]

    tool = WriteFileTool(tmp_path)
    rel_path = "a.txt"
    target = tmp_path / rel_path

    with team_execution_context(
        TeamExecutionContext(
            role="teammate",
            team_id=team_id,
            member_id="member-1",
            runtime=runtime,
        )
    ):
        out = await tool.execute(rel_path, "first")
    assert "Wrote" in out
    assert target.exists()

    with team_execution_context(
        TeamExecutionContext(
            role="teammate",
            team_id=team_id,
            member_id="member-2",
            runtime=runtime,
        )
    ):
        out = await tool.execute(rel_path, "second")
    assert "File lock conflict" in out

    await runtime.release_member_locks("member-1")
    with team_execution_context(
        TeamExecutionContext(
            role="teammate",
            team_id=team_id,
            member_id="member-2",
            runtime=runtime,
        )
    ):
        out = await tool.execute(rel_path, "second")
    assert "Wrote" in out

    await runtime.cleanup_team(actor_role="lead")


@pytest.mark.asyncio
async def test_integration_orchestration_loop(tmp_path):
    runtime = TeamRuntime(config=TeamsConfig(), workdir=tmp_path)
    runtime.set_agent_factory(lambda _team_id, _member_id: FakeWorkerAgent())
    await runtime.create_team(actor_role="lead", team_name="demo", member_count=1)

    task = await runtime.create_task(
        actor_role="lead",
        actor_id="lead",
        title="build",
        details="finish task",
        assignee_id="member-1",
    )
    assert task["task_id"].startswith("task-")

    saw_idle = False
    for _ in range(5):
        events = await runtime.wait_events(timeout_sec=1, max_events=20)
        if any(event.get("type") == "teammate_idle" for event in events):
            saw_idle = True
            break
    assert saw_idle

    completed = await runtime.complete_task(
        actor_role="lead",
        actor_id="lead",
        task_id=task["task_id"],
        note="summarized",
    )
    assert completed["status"] == "completed"

    await runtime.cleanup_team(actor_role="lead")
    summary = await runtime.get_summary()
    assert not summary["active"]


@pytest.mark.asyncio
async def test_cleanup_team_closes_teammate_clients(tmp_path):
    runtime = TeamRuntime(config=TeamsConfig(), workdir=tmp_path)
    created_agents: list[ClosableFakeWorkerAgent] = []

    def _factory(_team_id, _member_id):
        agent = ClosableFakeWorkerAgent()
        created_agents.append(agent)
        return agent

    runtime.set_agent_factory(_factory)
    await runtime.create_team(actor_role="lead", team_name="demo", member_count=2)
    await runtime.cleanup_team(actor_role="lead")

    assert len(created_agents) == 2
    assert all(agent.close_calls == 1 for agent in created_agents)
