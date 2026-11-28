"""Inspect imported and live MiniBot records through the Trace endpoint."""

import json
from pathlib import Path

import httpx
import pytest

from src.minibot.server import app as server
from src.minibot.server.manager import AgentManager
from src.minibot.traces import RunTrace
from tests.test_agent_streaming import _response
from tests.test_task_regression import ScriptedProvider, tool_turn
from tests.test_tool_outcomes import TurnClient, call


@pytest.fixture()
def trace_server(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setenv("MINIBOT_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    manager = AgentManager(workdir=workdir)
    manager._base_config.trace_dir = home / "traces"
    manager._base_config.mcp.enabled = False
    manager._base_config.teams.enabled = False
    manager._base_config.hooks.enabled = False
    monkeypatch.setattr(server, "AgentManager", lambda **_kwargs: manager)
    return manager, httpx.ASGITransport(app=server.create_app(workdir=workdir))


def _fields(event):
    assert all(field["source"] == "session" for field in event["fields"])
    return {field["label"]: field["value"] for field in event["fields"]}


@pytest.mark.asyncio
async def test_scoped_trace_projects_existing_chat_with_exact_invocations(trace_server, monkeypatch):
    manager, transport = trace_server
    bot_id = (await manager.create_bot(name="Trace"))["bot_id"]
    sessions = manager.sessions_for(bot_id)
    session_id = sessions.create()
    bot = await manager.get_bot(bot_id, session_id)
    monkeypatch.setattr("src.minibot.subagents.executor.rich_enabled", lambda: False)
    prompts = ("核对第一个证据", "核对第二个证据")
    calls = [
        call("Task", json.dumps({"description": prompt, "prompt": prompt, "agent_type": "code"}), id="repeated")
        for prompt in prompts
    ]
    child_outputs = ("证据甲可用", "证据乙不可用")
    bot.agent.client = TurnClient(calls)
    bot.agent.subagent_executor.client = ScriptedProvider(
        _response(content=child_outputs[0]),
        tool_turn(call("missing_trace_test_tool")),
        _response(content=child_outputs[1]),
    )
    uri = f"/api/bots/{bot_id}/sessions/{session_id}"
    user = "请检查两个证据"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        streamed = await client.post(
            f"/api/bots/{bot_id}/stream", json={"session_id": session_id, "prompt": user},
        )
        assert streamed.status_code == 200
        assert "assistant_end" in streamed.text
        response = await client.get(uri + "/trace")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get(f"/api/bots/default/sessions/{session_id}/trace")).status_code == 404
    snapshot = response.json()
    assert snapshot["schema_version"] == 1
    assert snapshot["source"] == {"kind": "minibot", "id": bot_id}
    assert snapshot["recording"]
    run, = snapshot["runs"]
    assert run["title"] == user
    assert run["status"] == "completed"
    events = run["events"]
    assert events[0]["kind"] == "message" and events[0]["start_ms"] is None
    assert events[0]["evidence"]["message_id"] == run["user_message_id"]
    parent_tools = [event for event in events if event["label"] == "Task"]
    children = [event for event in events if event["kind"] == "agent"]
    assert [event["parent_id"] for event in children] == [event["id"] for event in parent_tools]
    assert len({event["id"] for event in parent_tools}) == 2
    assert {event["evidence"]["tool_call_id"] for event in parent_tools} == {"repeated"}
    assert [_fields(event) for event in children] == [
        {"Prompt": prompt, "Return": output} for prompt, output in zip(prompts, child_outputs, strict=True)
    ]
    failed, = [event for event in events if event["label"] == "missing_trace_test_tool"]
    assert failed["is_error"] and failed["parent_id"] == children[1]["id"]
    assert not _fields(failed)
    assert "Delegate tasks" in {event["label"] for event in events}
    assert "Generate reply" in {event["label"] for event in events}
    failed_attempts = [event for event in events if event["kind"] == "model" and event["status"] == "failed"]
    assert failed_attempts and all(not _fields(event) for event in failed_attempts)
    final, = [event for event in events if event["label"] == "Assistant"]
    assert _fields(final)["Message"] == "done"
    raw, = Path(manager._base_config.trace_dir).glob("*.jsonl")
    disk = json.dumps([json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()], ensure_ascii=False)
    for text in (user, *prompts, *child_outputs, "done"):
        assert text not in disk


@pytest.mark.asyncio
async def test_trace_scope_rejects_ambiguous_legacy_and_preserves_partial_history(trace_server, tmp_path, monkeypatch):
    manager, transport = trace_server
    bot_id = (await manager.create_bot(name="Trace"))["bot_id"]
    session_id = manager.sessions_for(bot_id).create()
    trace_dir = Path(manager._base_config.trace_dir)
    message_id = "message-that-must-not-join-legacy"
    manager.sessions_for(bot_id).append_message(session_id, {
        "role": "user", "content": "Do not project", "message_id": message_id,
    })
    legacy = RunTrace(trace_dir, session_id, user_message_id=message_id)
    legacy.close("completed")
    partial = RunTrace(trace_dir, session_id, bot_id=bot_id)
    partial.observe({"type": "tool_call", "tool_name": "unfinished", "tool_call_id": "call"})
    partial.close("completed")
    partial_path = trace_dir / f"{partial.run_id}.jsonl"
    rows = partial_path.read_text(encoding="utf-8").splitlines(keepends=True)
    partial_path.write_text("".join([*rows[:2], "{broken\n", *rows[2:]]), encoding="utf-8")
    external = RunTrace(tmp_path / "external", session_id, bot_id=bot_id)
    external.close("completed")
    name = f"{external.run_id}.jsonl"
    (trace_dir / name).symlink_to(tmp_path / "external" / name)
    original_open = Path.open

    def require_encoding(path, *args, **kwargs):
        if path == partial_path:
            assert kwargs.get("encoding") == "utf-8"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", require_encoding)
    target = f"/api/bots/{bot_id}/sessions"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(target + "/%2A/trace")).status_code == 404
        assert (await client.get(target + "/%5C/trace")).status_code == 404
        assert (await client.get(f"/api/bots/unknown/sessions/{session_id}/trace")).status_code == 404
        response = await client.get(f"{target}/{session_id}/trace")
        assert response.status_code == 200
        projected = {run["id"]: run for run in response.json()["runs"]}
        assert set(projected) == {legacy.run_id, partial.run_id}
        assert projected[legacy.run_id]["title"] is None
        assert not _fields(projected[legacy.run_id]["events"][0])
        incomplete = projected[partial.run_id]
        assert incomplete["status"] == "incomplete" and incomplete["duration_ms"] is None
        unfinished, = incomplete["events"]
        assert unfinished["label"] == "unfinished" and unfinished["duration_ms"] is None

        manager.sessions_for("default").store.create(session_id)
        mine = RunTrace(trace_dir, session_id, bot_id="default")
        mine.close("completed")
        other = await client.get(f"/api/bots/default/sessions/{session_id}/trace")
        assert [run["id"] for run in other.json()["runs"]] == [mine.run_id]
        response = await client.get(f"{target}/{session_id}/trace")
        assert [run["id"] for run in response.json()["runs"]] == [partial.run_id]

        manager._base_config.trace_dir = None
        disabled = await client.get(f"{target}/{session_id}/trace")
        assert disabled.json()["recording"] is False and disabled.json()["runs"] == []


@pytest.mark.asyncio
async def test_trace_retains_exact_question_and_linked_resume(trace_server, monkeypatch):
    manager, transport = trace_server
    bot_id = (await manager.create_bot(name="Trace"))["bot_id"]
    session_id = manager.sessions_for(bot_id).create()
    bot = await manager.get_bot(bot_id, session_id)
    question = "请确认要处理哪一页？"
    response = "第三页"
    bot.agent.client = TurnClient([call("askuserquestion", json.dumps({"prompt": question}))])
    bot_uri = f"/api/bots/{bot_id}"
    uri = f"{bot_uri}/sessions/{session_id}"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        paused = await client.post(
            bot_uri + "/stream", json={"session_id": session_id, "prompt": "核对稿件"},
        )
        assert "ask_user_question" in paused.raise_for_status().text
        first, = (await client.get(uri + "/trace")).raise_for_status().json()["runs"]
        assert first["status"] == "waiting_for_user"
        wait, = [event for event in first["events"] if event["kind"] == "wait"]
        assert wait["duration_ms"] is None and _fields(wait) == {"Question": question}
        assert any(event["label"] == "Ask user" for event in first["events"])

        original_get_bot = manager.get_bot

        async def resumed_bot(*args):
            resumed = await original_get_bot(*args)
            resumed.agent.client = TurnClient([])
            return resumed

        monkeypatch.setattr(manager, "get_bot", resumed_bot)
        answered = await client.post(uri + "/answer/stream", json={
            "question_id": "call-1", "answer_text": response,
        })
        assert "assistant_end" in answered.raise_for_status().text
        original, resumed = (await client.get(uri + "/trace")).raise_for_status().json()["runs"]
        assert original["id"] == first["id"]
        assert resumed["parent_run_id"] == first["id"]
        assert resumed["status"] == "completed" and resumed["title"] == response
        assert _fields(resumed["events"][0]) == {"Message": response}
