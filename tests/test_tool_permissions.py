import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.minibot.agent import Agent
from src.minibot.config import RuntimeBudgetConfig, ToolPermissionsConfig
from src.minibot.sdk import Minibot
from src.minibot.tools.permissions import parse_review
from tests.test_agent_streaming import _response
from tests.test_bash_interrupt import config
from tests.test_task_regression import ScriptedProvider, tool_turn
from tests.test_tool_outcomes import call


def make_config(workspace: Path, mode="manual", **kwargs):
    cfg = config(workspace, backend="disabled")
    cfg.tool_permissions = ToolPermissionsConfig(mode=mode, **kwargs)
    return cfg


def scripted(bot: Minibot, *responses):
    bot.agent.client = ScriptedProvider(*responses)
    return bot


def write(path: str, content: str = "approved payload"):
    return tool_turn(call("write_file", json.dumps({"path": path, "content": content})))


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_stream_approval_resolves_exactly_once(tmp_path, approved):
    target = tmp_path / "report.txt"
    cfg = make_config(tmp_path)
    cfg.trace_dir = tmp_path / ".traces"
    bot = scripted(Minibot(config=cfg), write("report.txt"), _response(content="Finished."))
    hooks = []

    async def pre_hook(name, args):
        hooks.append((name, args["content"]))
        args["content"] = "changed after approval"
        return False, None

    bot.agent.hook_manager.trigger_pre_tool_call = pre_hook
    async with asyncio.timeout(5):
        observed = []
        async for event in bot.stream("Write approved payload to report.txt."):
            observed.append(event)
            if event["type"] == "tool_approval_request":
                assert hooks == []
                assert not target.exists()
                assert json.loads(event["approval_preview"]) == {"path": "report.txt", "content": "approved payload"}
                with pytest.raises(ValueError, match="No live"):
                    bot.submit_tool_approval("wrong-id", True)
                bot.submit_tool_approval(event["approval_id"], approved)
                with pytest.raises(ValueError, match="No live"):
                    bot.submit_tool_approval(event["approval_id"], True)

    resolved = [event for event in observed if event["type"] == "tool_approval_resolved"]
    assert [event["approved"] for event in resolved] == [approved]
    assert [event["approval_source"] for event in resolved] == ["human"]
    outcome = next(event for event in observed if event["type"] == "tool_result")
    assert outcome["tool_status"] == ("success" if approved else "refused")
    assert target.exists() == approved
    if approved:
        assert target.read_text(encoding="utf-8") == "approved payload"
        assert hooks == [("write_file", "approved payload")]
    else:
        assert hooks == []
    trace = next((tmp_path / ".traces").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "approved payload" not in trace
    assert "report.txt" not in trace
    events = [json.loads(line) for line in trace.splitlines()]
    assert next(item for item in events if item["event"] == "tool_approval")["approved"] == approved


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["timeout", "cancel", "detach"])
async def test_unresolved_or_interrupted_stream_never_executes_pending_tool(tmp_path, how):
    target = tmp_path / "report.txt"
    cfg = make_config(tmp_path, timeout=0.05 if how == "timeout" else 10)
    bot = scripted(Minibot(config=cfg), write("report.txt"), _response(content="Finished."))
    async with asyncio.timeout(5):
        reader = bot.stream("Write to report.txt.")
        approval = None
        try:
            async for event in reader:
                if event["type"] == "tool_approval_request":
                    approval = event
                    if how == "cancel":
                        bot.cancel()
                    if how == "detach":
                        break
        finally:
            await reader.aclose()
        state = bot._active_stream
        if state is not None and state.producer is not None:
            await state.producer
        assert approval is not None
        with pytest.raises(ValueError, match="No live"):
            bot.submit_tool_approval(approval["approval_id"], True)
        assert not target.exists()


@pytest.mark.asyncio
async def test_chat_without_live_handler_refuses_and_explicit_callback_can_approve(tmp_path):
    cfg = make_config(tmp_path)
    bot = scripted(Minibot(config=cfg), write("pending.txt"), _response(content="Finished."))
    await asyncio.wait_for(bot.chat("Write pending.txt."), 5)
    assert not (tmp_path / "pending.txt").exists()

    requests = []

    async def approve(request):
        requests.append(request)
        return True

    bot = scripted(
        Minibot(config=cfg, tool_approval_handler=approve),
        write("approved.txt"), _response(content="Finished."),
    )
    await bot.chat("Write approved.txt.")
    assert (tmp_path / "approved.txt").is_file()
    assert len(requests) == 1
    assert requests[0].actor == "solo"


class ReviewClient(ScriptedProvider):
    def __init__(self, *responses):
        super().__init__(*responses)
        self.calls = []

    async def create_message_async(self, *, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return await super().create_message_async(messages=messages, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_reply", "human_approves", "written", "source"),
    [
        (_response(content='{"decision":"approve"}'), False, True, "auto_review"),
        (_response(content='{"decision":"deny"}'), True, False, "auto_review"),
        (_response(content='{"decision":"approve","decision":"approve"}'), True, True, "human"),
        (_response(content="approve"), False, False, "human"),
        ({"output_text": '{"decision":"approve"}'}, False, False, "human"),
    ],
)
async def test_auto_review_uses_isolated_prompt_and_strict_decision(
    tmp_path, model_reply, human_approves, written, source,
):
    cfg = make_config(tmp_path, mode="auto_review", auto_review_paths=["notes/**"])
    requests = []

    async def human(request):
        requests.append(request)
        return human_approves

    bot = Minibot(config=cfg, tool_approval_handler=human)
    bot.agent.client = ReviewClient(
        write("notes/report.txt"), model_reply, _response(content="Finished."),
    )
    events = [event async for event in bot.stream("Write approved payload to notes/report.txt.")]
    assert (tmp_path / "notes" / "report.txt").exists() == written
    assert len(requests) == (1 if source == "human" else 0)
    review_messages, review_args = bot.agent.client.calls[1]
    assert review_args["tools"] is None
    assert review_messages[0]["role"] == "user"
    assert len(review_messages) == 1
    assert json.loads(review_messages[0]["content"])["arguments"]["path"] == "notes/report.txt"
    assert next(event for event in events if event["type"] == "tool_approval_resolved")["approval_source"] == source


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["outside-scope.txt", "notes/.env", "../escapes.txt"])
async def test_auto_review_cannot_approve_outside_its_scope(tmp_path, path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = make_config(workspace, mode="auto_review", auto_review_paths=["notes/**"])
    bot = Minibot(config=cfg)
    bot.agent.client = ReviewClient(write(path), _response(content="Finished."))
    await bot.chat("Write a file.")
    assert len(bot.agent.client.calls) == 2
    assert not (workspace / path).exists()


@pytest.mark.asyncio
async def test_auto_review_cannot_authorize_custom_tool_with_builtin_name(tmp_path):
    cfg = make_config(tmp_path, mode="auto_review", auto_review_paths=["notes/**"])
    ran = []

    def write_file(path: str, content: str) -> str:
        ran.append((path, content))
        return "unexpected call"

    bot = Minibot(config=cfg, tools=[write_file])
    bot.agent.client = ReviewClient(write("notes/report.txt"), _response(content="Finished."))
    await bot.chat("Write a file.")
    assert len(bot.agent.client.calls) == 2
    assert ran == []


@pytest.mark.asyncio
async def test_policy_deny_precedence_and_reviewer_budget(tmp_path):
    cfg = make_config(tmp_path, mode="auto_review", rules={"write_file": "deny"}, auto_review_paths=["notes/**"])
    bot = Minibot(config=cfg)
    bot.agent.client = ReviewClient(write("notes/report.txt"), _response(content="Finished."))
    await bot.chat("Write a file.")
    assert len(bot.agent.client.calls) == 2
    assert not (tmp_path / "notes").exists()

    cfg.tool_permissions = ToolPermissionsConfig(mode="auto_review", auto_review_paths=["notes/**"])
    cfg.runtime_budget = RuntimeBudgetConfig(max_llm_calls=1)
    bot = Minibot(config=cfg)
    bot.agent.client = ReviewClient(write("notes/report.txt"))
    result = await bot.chat("Write a file.")
    assert "budget_exceeded" in result.assistant_text
    assert len(bot.agent.client.calls) == 1
    assert not (tmp_path / "notes").exists()


@pytest.mark.asyncio
async def test_auto_approval_cannot_be_redirected_by_retargeted_symlink(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    internal = workspace / "notes"
    internal.mkdir()
    target = internal / "report.txt"
    try:
        (workspace / "alias").symlink_to(internal, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    cfg = make_config(workspace, mode="auto_review", auto_review_paths=["notes/**"])
    bot = Minibot(config=cfg)

    def reviewer(_messages):
        internal.rename(workspace / "moved")
        internal.symlink_to(notes, target_is_directory=True)
        return _response(content='{"decision":"approve"}')

    bot.agent.client = ReviewClient(write("alias/report.txt"), reviewer, _response(content="Finished."))
    await bot.chat("Write the report.")
    assert not target.exists()
    assert not (notes / "report.txt").exists()
    assert not (workspace / "moved" / "report.txt").exists()


@pytest.mark.asyncio
async def test_subagent_asks_through_parent_and_background_teammate_fails_closed(tmp_path):
    cfg = make_config(tmp_path, rules={"Task": "allow"})
    task = call("Task", json.dumps({"description": "Create report", "prompt": "Write the report", "agent_type": "code"}))
    bot = scripted(Minibot(config=cfg), tool_turn(task), _response(content="Finished."))
    bot.agent.subagent_executor.client = ScriptedProvider(write("delegated.txt"), _response(content="Finished."))
    async with asyncio.timeout(5):
        async for event in bot.stream("Ask your subagent to create delegated.txt."):
            if event["type"] == "tool_approval_request":
                assert event["approval_actor"] == "subagent:code"
                assert not (tmp_path / "delegated.txt").exists()
                bot.submit_tool_approval(event["approval_id"], True)
    assert (tmp_path / "delegated.txt").exists()

    teammate = bot.agent._create_teammate_agent("team-id", "member-id")
    teammate.client = ScriptedProvider(write("orphaned.txt"), _response(content="Finished."))
    await teammate.run_loop([{"role": "user", "content": "Team task"}])
    assert not (tmp_path / "orphaned.txt").exists()


@pytest.mark.asyncio
async def test_approval_endpoint_validates_and_does_not_take_stream_lock(tmp_path, monkeypatch):
    from src.minibot.server import app as server
    from src.minibot.server.manager import AgentManager

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MINIBOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    manager = AgentManager(workdir=workspace)
    manager._base_config.tool_permissions = ToolPermissionsConfig(mode="manual")
    manager._base_config.mcp.enabled = False
    manager._base_config.teams.enabled = False
    bot_id = (await manager.create_bot(name="Approval API"))["bot_id"]
    session_id = manager.sessions_for(bot_id).create()
    bot = await manager.get_bot(bot_id, session_id)
    scripted(bot, write("via-api.txt"), _response(content="Finished."))
    monkeypatch.setattr(server, "AgentManager", lambda **kwargs: manager)
    lock = await manager.session_lock(bot_id, session_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.create_app(workdir=workspace)),
                                 base_url="http://test") as api, lock, asyncio.timeout(5):
        async for event in bot.stream("Write via-api.txt."):
            if event["type"] != "tool_approval_request":
                continue
            target = f"/api/bots/{bot_id}/sessions/{session_id}/tool-approvals/{event['approval_id']}"
            wrong = target.replace(f"/sessions/{session_id}/", "/sessions/wrong/")
            assert (await api.post(wrong, json={"approved": True})).status_code == 409
            assert (await api.post(target, json={"approved": "true"})).status_code == 422
            assert (await api.post(target, json={"approved": True})).status_code == 200
            assert (await api.post(target, json={"approved": True})).status_code == 409
    assert (workspace / "via-api.txt").exists()


@pytest.mark.parametrize("text", ['[["decision","approve"]]', '{"decision":"approve"}\nextra', "[]", "null"])
def test_malformed_review_is_never_an_approval(text):
    assert parse_review(text) == "ask"


@pytest.mark.asyncio
async def test_live_teammate_approval_uses_owner_id_and_trace(tmp_path):
    cfg = make_config(tmp_path, rules={"dispatch_team_work": "allow"})
    cfg.trace_dir = tmp_path / ".traces"
    release_team = asyncio.Event()

    async def dispatch_team_work() -> str:
        release_team.set()
        await worker
        return "finished"

    bot = scripted(
        Minibot(config=cfg, tools=[dispatch_team_work]),
        tool_turn(call("dispatch_team_work")), _response(content="Finished."),
    )
    teammate = bot.agent._create_teammate_agent("team-1", "member-2")
    teammate.client = ScriptedProvider(write("teammate.txt"), _response(content="Finished."))

    async def existing_teammate_worker():
        await release_team.wait()
        await teammate.run_loop([{"role": "user", "content": "Delegated work"}])

    worker = asyncio.create_task(existing_teammate_worker())
    own_run = None
    async with asyncio.timeout(5):
        async for event in bot.stream("Ask a teammate to write teammate.txt."):
            if event["type"] == "tool_approval_request":
                assert event["approval_actor"] == "teammate:member-2"
                assert not (tmp_path / "teammate.txt").exists()
                own_run = bot._active_stream.run_id
                bot.submit_tool_approval(event["approval_id"], True)
    assert (tmp_path / "teammate.txt").exists()
    assert own_run is not None
    rows = (cfg.trace_dir / f"{own_run}.jsonl").read_text(encoding="utf-8")
    assert [json.loads(row)["event"] for row in rows.splitlines()].count("tool_approval") == 1
