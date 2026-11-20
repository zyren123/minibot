"""Verify journal and replay semantics against real process termination."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from src.minibot import Minibot
from src.minibot.config import BashConfig, Config, LLMConfig, RuntimeBudgetConfig, ToolPermissionsConfig
from src.minibot.config.schema import HooksConfig, MemoryConfig, SessionConfig, TeamsConfig, ToolsConfig
from src.minibot.core.run_store import DurableRunBusy, DurableRunError, DurableRunPaused, RunJournal
from src.minibot.core.runtime_budget import RuntimeBudgetExceeded
from src.minibot.core.workspace import legacy_workspace_identity
from src.minibot.server.manager import AgentManager


def append_line(path, line):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def lines(path):
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def bot_for(root, *, approvals=False, session_id="recovery", config=None, **kwargs):
    if config is None:
        config = Config(
            workdir=root, app_home=root / "home",
            llm=LLMConfig(api_key="durable-run-test", stream_enabled=False),
            memory=MemoryConfig(enabled=False), teams=TeamsConfig(enabled=False),
            hooks=HooksConfig(enabled=False), session=SessionConfig(sessions_dir=str(root / "sessions")),
            tool_permissions=ToolPermissionsConfig(mode="manual", rules={"effect": "ask"}) if approvals
            else ToolPermissionsConfig(),
        )

    def effect(label: str) -> str:
        append_line(root / "effects.txt", label)
        return label if label != "first" else label + " 日志" * 20_000

    return Minibot(config=config, session_id=session_id, tools=[effect], durable_execution=True, **kwargs)


def call(name, arguments, *, call_id="duplicate-provider-id"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def response(*calls):
    return {"choices": [{"finish_reason": "tool_calls" if calls else "stop",
                        "message": {"content": "" if calls else "done", "tool_calls": list(calls)}}]}


def use_model(bot, root, *, before_request=None, calls=None):
    if calls is None:
        calls = [call("effect", {"label": "first"}), call("effect", {"label": "second"})]

    async def model(*, messages, **kwargs):
        if before_request is not None:
            before_request()
        append_line(root / "model.jsonl", json.dumps(messages))
        return response() if any(
            msg.get("role") == "tool" or msg.get("content") == "Antwort" for msg in messages
        ) else response(*calls)

    async def no_stream(**kwargs):
        raise RuntimeError("Test provider uses non-streaming output")

    bot.agent.client.create_message_async = model
    bot.agent.client.create_message_stream_async = no_stream


def worker(root, fault):
    def stop(detail="ready"):
        (root / "ready").write_text(detail, encoding="utf-8")
        while True:
            time.sleep(1)

    async def wait_approval(request):
        stop(request.approval_id)

    async def grant_approval(request):
        return True

    bot = bot_for(
        root, approvals=fault in {"approval", "after_approval"},
        tool_approval_handler=grant_approval if fault == "after_approval" else wait_approval,
    )
    calls = None
    if fault == "readonly":
        (root / "file.txt").write_text("read-only", encoding="utf-8")
        calls = [call("read_file", {"path": "file.txt"}), call("effect", {"label": "second"})]
    use_model(bot, root, before_request=stop if fault == "before_model" else None, calls=calls)
    event_to_stop = {
        "before_dispatch": "tool_call",
        "after_journal": "tool_result",
        "after_approval": "tool_approval_resolved",
    }.get(fault)
    if event_to_stop:
        original_emit = bot.agent._emit

        async def emit(event, **kwargs):
            if event.get("type") == event_to_stop:
                stop(event.get("approval_id", "ready"))
            await original_emit(event, **kwargs)

        bot.agent._emit = emit
    if fault in {"after_effect", "readonly"}:
        original_finish = RunJournal.finish_step

        def finish(self, step, result, **kwargs):
            stop()
            return original_finish(self, step, result, **kwargs)

        RunJournal.finish_step = finish
    asyncio.run(bot.chat("Run the batch"))


async def wait_for_file(path, child):
    for _ in range(500):
        if child.poll() is not None:
            pytest.fail(f"Fault-injection child exited first: {child.communicate()}")
        if path.exists() and path.stat().st_size > 0:
            return path.read_text(encoding="utf-8")
        await asyncio.sleep(0.02)
    pytest.fail("The child missed the fault-injection checkpoint")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "effects_before", "steps_before", "run_status"),
    [
        ("before_model", [], [], "interrupted"),
        ("before_dispatch", [], ["planned", "planned"], "interrupted"),
        ("after_effect", ["first"], ["recovery_required", "planned"], "recovery_required"),
        ("after_journal", ["first"], ["completed", "planned"], "interrupted"),
        ("approval", [], ["planned", "planned"], "interrupted"),
        ("after_approval", [], ["planned", "planned"], "interrupted"),
        ("readonly", [], ["executing", "planned"], "interrupted"),
    ],
)
async def test_restart_replays_only_unfinished_safe_work(tmp_path, fault, effects_before, steps_before, run_status):
    approvals = []
    uses_approval = fault in {"approval", "after_approval"}

    async def approve(request):
        approvals.append(request.approval_id)
        return True

    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), fault],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    try:
        old_approval = await wait_for_file(tmp_path / "ready", child)
        bot = bot_for(tmp_path, approvals=uses_approval, tool_approval_handler=approve)
        [live_run] = bot.list_runs(incomplete_only=True)
        assert live_run.owner_pid == child.pid
        assert live_run.status == ("waiting" if uses_approval else "running")
        with pytest.raises(DurableRunBusy):
            await bot.resume_run(live_run.run_id)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
    assert child.returncode < 0 or os.name == "nt"

    [run] = bot.list_runs(incomplete_only=True)
    assert run.status == run_status
    assert run.owner_pid is None
    steps = bot.list_run_steps(run.run_id)
    assert [step.status for step in steps] == steps_before
    assert len({step.step_id for step in steps}) == len(steps)
    assert lines(tmp_path / "effects.txt") == effects_before
    model_requests_before_resume = len(lines(tmp_path / "model.jsonl"))

    use_model(bot, tmp_path)
    if fault == "after_effect":
        with pytest.raises(DurableRunError, match="recovery_required"):
            await bot.resume_run(run.run_id)
        assert lines(tmp_path / "effects.txt") == ["first"]
        other = bot_for(tmp_path, session_id="other")
        with pytest.raises(DurableRunError, match="unavailable"):
            other.resolve_run_step(run.run_id, steps[0].step_id, "verified externally")
        bot.resolve_run_step(run.run_id, steps[0].step_id, "verified externally")

    result = await bot.resume_run(run.run_id)
    assert result.assistant_text == "done"
    assert lines(tmp_path / "effects.txt") == (["second"] if fault == "readonly" else ["first", "second"])
    assert bot.list_runs(incomplete_only=True) == []
    [finished] = bot.list_runs()
    assert (finished.status, finished.segment) == ("completed", 2)
    first, second = bot.list_run_steps(run.run_id)
    assert first.status == second.status == "completed"
    assert first.provider_call_id == second.provider_call_id
    assert first.step_id != second.step_id
    model_requests = [json.loads(row) for row in lines(tmp_path / "model.jsonl")]
    assert len(model_requests) - model_requests_before_resume == (2 if fault == "before_model" else 1)
    request_batch = model_requests[-1][1:]
    assert [msg["role"] for msg in request_batch] == ["assistant", "tool", "tool"]
    assert [item["id"] for item in request_batch[0]["tool_calls"]] == [first.provider_call_id, second.provider_call_id]
    assert [item["tool_call_id"] for item in request_batch[1:]] == [first.provider_call_id, second.provider_call_id]
    if uses_approval:
        assert len(approvals) == 2 and old_approval not in approvals
    if fault not in {"after_effect", "readonly"}:
        assert first.artifact_id
        assert first.artifact_id in request_batch[1]["content"]


@pytest.mark.asyncio
async def test_pause_at_boundary_and_completed_attach_preserves_newer_history(tmp_path):
    bot = bot_for(tmp_path)
    use_model(bot, tmp_path)
    original_emit = bot.agent._emit

    async def pause_after_first(event, **kwargs):
        if event.get("type") == "tool_result":
            [active] = bot.list_runs(incomplete_only=True)
            assert bot.pause_run(active.run_id)
        await original_emit(event, **kwargs)

    bot.agent._emit = pause_after_first
    with pytest.raises(DurableRunPaused):
        await bot.chat("Run the batch")
    assert lines(tmp_path / "effects.txt") == ["first"]
    [run] = bot.list_runs(incomplete_only=True)
    assert run.status == "interrupted"
    with pytest.raises(DurableRunError, match="unfinished"):
        await bot.chat("Must not enter history")
    assert all(msg["content"] != "Must not enter history" for msg in bot.messages)

    bot.agent._emit = original_emit
    config = replace(bot.config, tool_permissions=ToolPermissionsConfig(mode="manual", default="deny"))
    resumed = bot_for(tmp_path, config=config)
    use_model(resumed, tmp_path)
    await resumed.resume_run(run.run_id)
    assert resumed.list_run_steps(run.run_id)[1].result["tool_status"] == "refused"
    assert lines(tmp_path / "effects.txt") == ["first"]
    await resumed.chat("New turn")
    await resumed.resume_run(run.run_id)
    assert any(msg["content"] == "New turn" for msg in resumed.messages)


@pytest.mark.asyncio
async def test_ask_user_survives_reopen_and_keeps_rest_of_batch(tmp_path):
    calls = [call("askuserquestion", {"prompt": "Choose a value"}), call("effect", {"label": "second"})]
    bot = bot_for(tmp_path)
    use_model(bot, tmp_path, calls=calls)
    events = [event async for event in bot.stream("Ask first", stop_on_ask_user_question=True)]
    [question] = [event for event in events if event["type"] == "ask_user_question"]
    [run] = bot.list_runs(incomplete_only=True)
    assert run.status == "waiting"
    assert run.wait_reason == "user_question"
    assert lines(tmp_path / "effects.txt") == []

    reopened = bot_for(tmp_path)
    use_model(reopened, tmp_path, calls=calls)
    resumed = [event async for event in reopened.resume_pending_question_stream(
        durable_run_id=run.run_id, question_id=question["question_id"], answer_text="Antwort",
    )]
    assert any(event["type"] == "ask_user_answer_received" for event in resumed)
    assert reopened.list_runs()[0].status == "completed"
    assert lines(tmp_path / "effects.txt") == ["second"]
    assert [msg["content"] for msg in reopened.messages if msg["role"] == "user"] == ["Ask first", "Antwort"]
    assert reopened._session_manager.load_runtime_state(reopened.session_id) is None
    model_request = json.loads(lines(tmp_path / "model.jsonl")[-1])
    assert [message["role"] for message in model_request] == ["user", "assistant", "tool", "user"]
    assert model_request[1]["tool_calls"][0]["function"]["name"] == "effect"


@pytest.mark.asyncio
async def test_budget_resets_at_explicit_resume_boundary(tmp_path):
    bot = bot_for(tmp_path)
    bot.config.runtime_budget = RuntimeBudgetConfig(max_tool_calls=1)
    use_model(bot, tmp_path)
    with pytest.raises(RuntimeBudgetExceeded):
        await bot.chat("One tool per segment")
    [run] = bot.list_runs(incomplete_only=True)
    assert run.status == "interrupted"
    assert lines(tmp_path / "effects.txt") == ["first"]
    assert (await bot.resume_run(run.run_id)).assistant_text == "done"
    assert lines(tmp_path / "effects.txt") == ["first", "second"]


@pytest.mark.asyncio
async def test_webui_answer_resumes_the_same_durable_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIBOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "durable-run-test")
    manager = AgentManager(workdir=tmp_path)
    manager._base_config.durable_execution = True
    manager._base_config.mcp.enabled = False
    manager._base_config.memory.enabled = False
    manager._base_config.teams.enabled = False
    bot_id = (await manager.create_bot(name="Durable question"))["bot_id"]
    sessions = manager.sessions_for(bot_id)
    session_id = sessions.create()
    bot = await manager.get_bot(bot_id, session_id)
    use_model(bot, tmp_path, calls=[call("askuserquestion", {"prompt": "Choose a value"})])
    events = [event async for event in bot.stream("Ask first", stop_on_ask_user_question=True)]
    [question_event] = [event for event in events if event["type"] == "ask_user_question"]
    [run] = bot.list_runs(incomplete_only=True)
    manager._clear_session_cache(bot_id, session_id, preserve_lock=True)
    reopened = await manager.get_bot(bot_id, session_id)
    use_model(reopened, tmp_path)
    question = await manager.pending_question_snapshot(bot_id, session_id)
    assert question["question_id"] == question_event["question_id"]
    history = sessions.load(session_id)
    sidecar = sessions.load_runtime_state(session_id)
    with pytest.raises(ValueError, match="Resume or abandon unfinished durable run"):
        await manager.regenerate_message_turn(bot_id, session_id, question["message_id"])
    with pytest.raises(ValueError, match="Resume or abandon unfinished durable run"):
        await manager.delete_message_turn(bot_id, session_id, question["message_id"])
    with pytest.raises(ValueError, match="Resume or abandon unfinished durable run"):
        await manager.delete_session(bot_id, session_id)
    with pytest.raises(DurableRunError, match="Resume or abandon unfinished durable run"):
        reopened.delete_session(session_id)
    assert sessions.load(session_id) == history
    assert sessions.load_runtime_state(session_id) == sidecar
    events = [event async for event in manager.submit_user_answer_stream(
        bot_id, session_id, {"question_id": question["question_id"], "answer_text": "Antwort"},
    )]
    assert any(event.get("content") == "done" for event in events)
    assert sessions.load_runtime_state(session_id) is None
    [finished] = reopened.list_runs()
    assert (finished.run_id, finished.status, finished.segment) == (run.run_id, "completed", 2)
    assert [message["content"] for message in sessions.load(session_id) if message["role"] == "user"] == [
        "Ask first", "Antwort",
    ]


@pytest.mark.asyncio
async def test_compaction_preserves_original_prompt_and_completed_tool_in_jsonl(tmp_path):
    bot = bot_for(tmp_path)
    bot.config.llm.max_context_tokens = 8_192
    bot.config.llm.max_tokens = 512
    reply = response()
    reply["usage"] = {"prompt_tokens": 6_700, "completion_tokens": 10, "total_tokens": 6_710}
    summary = response()
    summary["choices"][0]["message"]["content"] = "Compacted summary"
    replies = iter([response(call("effect", {"label": "second"})), reply, summary])

    async def model(**kwargs):
        return next(replies)

    bot.agent.client.create_message_async = model
    result = await bot.chat("Original unabridged prompt")

    path = bot._session_manager.store._find_path(bot.session_id)
    stored = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert result.assistant_text == "done"
    assert stored[0]["content"] == "Original unabridged prompt"
    assert any(message["role"] == "tool" and message["content"] == "second" for message in stored)
    assert any(message.get("is_compaction") for message in stored)
    assert stored[-1]["content"] == "done"


@pytest.mark.asyncio
async def test_scope_uses_configured_workspace_and_session_storage(tmp_path):
    bot = bot_for(tmp_path)
    use_model(bot, tmp_path)
    await bot.chat("Run the batch")
    [run] = bot.list_runs()

    local_with_unused_image = replace(bot.config, tools=ToolsConfig(bash=BashConfig(image="different")))
    assert legacy_workspace_identity(local_with_unused_image) == bot.agent.workspace_identity
    for config in (
        replace(bot.config, tools=ToolsConfig(bash=BashConfig(backend="disabled"))),
        replace(bot.config, session=SessionConfig(sessions_dir=str(tmp_path / "other-sessions"))),
        replace(bot.config, workdir=tmp_path / "other-workdir"),
    ):
        reopened = bot_for(tmp_path, config=config)
        assert reopened.list_runs() == []
        with pytest.raises(DurableRunError, match="unavailable"):
            await reopened.resume_run(run.run_id)


if __name__ == "__main__":
    worker(Path(sys.argv[1]), sys.argv[2])
