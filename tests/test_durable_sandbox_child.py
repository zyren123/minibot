"""Kill and restart the SDK with the real Docker workspace and a scripted provider."""

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from src.minibot import Minibot
from src.minibot.config import Config, HooksConfig, LLMConfig, MCPConfig, TeamsConfig
from src.minibot.config.schema import SessionConfig
from src.minibot.core.run_store import DurableRunBusy, DurableRunError, DurableRunPaused, RunJournal
from src.minibot.session.store import SessionStore
from src.minibot.tools.builtin import bash_execution
from tests.test_durable_execution import append_line, call, lines, response, wait_for_file


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("MINIBOT_TEST_DOCKER") != "1" or os.name != "posix",
        reason="set MINIBOT_TEST_DOCKER=1 for the POSIX Docker restart suite",
    ),
]


def bot_for(root, *, long_running=False):
    def host_only() -> str:
        return (root / "host.txt").read_text(encoding="utf-8")

    cfg = Config(
        workdir=root, app_home=root / "home", durable_execution=True,
        llm=LLMConfig(api_key="offline-model", stream_enabled=False),
        session=SessionConfig(sessions_dir=str(root / "sessions")),
        teams=TeamsConfig(enabled=False), trace_dir=root / "traces",
        hooks=HooksConfig(enabled=False), mcp=MCPConfig(enabled=False),
    )
    bot = Minibot(config=cfg, session_id="parent", tools=[host_only])

    async def model(*, messages, system, tools, **kwargs):
        results = [m for m in messages if m["role"] == "tool"]
        if "Working Directory: /workspace" not in system:
            assert "host_only" in {entry["function"]["name"] for entry in tools}
            assert bot.agent.memory_manager is not None
            assert "read_memory" in {entry["function"]["name"] for entry in tools}
            if results:
                return response()
            return response(
                call("host_only", {}, call_id="host"),
                call("Task", {"description": "Write and test", "prompt": "Child writes a file", "agent_type": "code"}),
            )

        append_line(root / "child-model.log", "continue" if results else "start")
        available = {entry["function"]["name"] for entry in tools}
        assert {"bash", "write_file", "read_file"} <= available
        assert {"Task", "host_only", "read_memory", "Skill"}.isdisjoint(available)
        if results:
            return response()
        shell = f"test ! -e '{root / 'host.txt'}' || exit 9; printf 'effect\\n' >> effects.txt"
        if long_running:
            shell += "; sleep 60; touch escaped-cancel"
        return response(call("bash", {"command": shell}, call_id="shell"))

    bot.agent.client.create_message_async = model
    return bot


def mark_and_stop(root, detail):
    (root / "ready").write_text(detail, encoding="utf-8")
    while True:
        time.sleep(1)


def worker(root, fault, parent_id=None):
    bot = bot_for(root, long_running=fault in {"owner", "running"})
    if fault == "identity":
        create = RunJournal.create_child

        def create_then_stop(journal, *args, **kwargs):
            child = create(journal, *args, **kwargs)
            mark_and_stop(root, child.run.run_id)

        RunJournal.create_child = create_then_stop

    if fault == "create_intent":
        reserve = RunJournal.reserve_container

        def reserve_then_stop(journal, name):
            reserve(journal, name)
            mark_and_stop(root, name)

        RunJournal.reserve_container = reserve_then_stop

    if fault in {"effect", "parent_result"}:
        finish = RunJournal.finish_step

        def stop_around_finish(journal, step, result, **kwargs):
            if fault == "effect" and step.name == "bash":
                mark_and_stop(root, journal.run_id)
            returned = finish(journal, step, result, **kwargs)
            if fault == "parent_result" and step.name == "Task":
                mark_and_stop(root, journal.child_for_step(step).run.run_id)
            return returned

        RunJournal.finish_step = stop_around_finish

    if fault == "child_result":
        finish_delivery = RunJournal.finish_child_delivery

        def stop_after_delivery(journal, receipt):
            finish_delivery(journal, receipt)
            mark_and_stop(root, journal.run_id)

        RunJournal.finish_child_delivery = stop_after_delivery

    if fault in {"owner", "contender"}:
        [child] = bot.list_child_runs(parent_id)
        try:
            asyncio.run(bot.agent.durable_subagents._run(child))
        except DurableRunBusy:
            assert fault == "contender"
        else:
            raise AssertionError("The competing process acquired the active child")
    else:
        asyncio.run(bot.chat("Parent delegates"))


def spawn(root, fault, parent_id=None, *, script=None):
    args = [sys.executable, str((script or Path(__file__)).resolve()), str(root), fault]
    if parent_id is not None:
        args.append(parent_id)
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )


def kill_worker(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGKILL)
    process.communicate(timeout=10)
    assert process.returncode == -signal.SIGKILL


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
    return result.stdout.strip()


def child_container(child, bot):
    store = bot.agent.run_store.for_child(child)
    # This snapshot is useful while a different process holds the execution lock.
    return RunJournal(store, child.run.session_id, child.run.run_id).pending_container()


async def wait_for_effect(child, worker=None):
    for _ in range(500):
        if worker is not None and worker.poll() is not None:
            pytest.fail(f"Worker exited before the shell effect: {worker.communicate()}")
        if lines(child.workspace_path / "effects.txt") == ["effect"]:
            return
        await asyncio.sleep(0.02)
    pytest.fail("The child Bash operation did not start")


@pytest.mark.parametrize("fault", ["identity", "effect", "child_result", "parent_result"])
async def test_restart_keeps_child_and_exactly_one_effect(tmp_path, fault):
    (tmp_path / "host.txt").write_text("宿主机", encoding="utf-8")
    process = spawn(tmp_path, fault)
    try:
        child_id = await wait_for_file(tmp_path / "ready", process)
    finally:
        kill_worker(process)

    bot = bot_for(tmp_path)
    [parent] = bot.list_runs()
    [child] = bot.list_child_runs(parent.run_id)
    assert child.run.run_id == child_id and child.run.session_id != parent.session_id
    assert parent.status == ("interrupted" if fault == "parent_result" else "waiting_child")
    assert lines(child.workspace_path / "effects.txt") == ([] if fault == "identity" else ["effect"])
    if fault in {"child_result", "parent_result"}:
        assert child.run.status == "completed"
    if fault == "parent_result":
        assert bot.list_run_steps(parent.run_id)[-1].status == "completed"
        assert all(msg.get("tool_name") != "Task" for msg in bot.messages)
    if fault == "effect":
        with pytest.raises(DurableRunPaused, match="recovery_required"):
            await bot.resume_run(parent.run_id)
        [step] = bot.list_child_run_steps(parent.run_id, child_id)
        assert step.status == "recovery_required"
        await bot.resolve_child_run_step(parent.run_id, child_id, step.step_id, "Verified exact file contents")
    result = await bot.resume_run(parent.run_id)
    assert result.assistant_text == "done"
    [finished_child] = bot.list_child_runs(parent.run_id)
    assert (finished_child.run.run_id, finished_child.run.status) == (child_id, "completed")
    assert lines(child.workspace_path / "effects.txt") == ["effect"]
    assert not (tmp_path / "effects.txt").exists()
    assert lines(tmp_path / "child-model.log") == ["start", "continue"]
    [result_message] = [msg for msg in result.messages if msg.get("tool_name") == "Task"]
    assert json.loads(result_message["content"])["child_run_id"] == child_id
    child_session = SessionStore(Path(child.run.sessions_dir)).load(child.run.session_id)
    assert child_session[-1]["content"] == "done"
    assert child_session[0]["content"] == "Child writes a file"
    await bot.agent.close_client()


async def test_same_child_lock_and_unconfirmed_docker_cleanup(tmp_path, monkeypatch):
    (tmp_path / "host.txt").write_text("host", encoding="utf-8")
    setup = spawn(tmp_path, "identity")
    try:
        await wait_for_file(tmp_path / "ready", setup)
    finally:
        kill_worker(setup)
    bot = bot_for(tmp_path)
    [parent] = bot.list_runs()
    [child] = bot.list_child_runs(parent.run_id)
    owner = spawn(tmp_path, "owner", parent.run_id)
    record = None
    try:
        await wait_for_effect(child, owner)
        record = child_container(child, bot)
        assert record.container_id
        contender = spawn(tmp_path, "contender", parent.run_id)
        stdout, stderr = await asyncio.to_thread(contender.communicate, timeout=10)
        assert contender.returncode == 0, (stdout, stderr)
    finally:
        kill_worker(owner)
    assert docker("inspect", "--format", "{{.State.Running}}", record.container_id) == "true"
    with pytest.raises(DurableRunError, match="child"):
        bot.abandon_run(parent.run_id)
    real_process = bash_execution._process

    async def removal_unavailable(executable, verb, *args, **kwargs):
        if verb == "rm":
            return bash_execution.ExecutionResult("", "injected daemon removal failure", 1)
        return await real_process(executable, verb, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(bash_execution, "_process", removal_unavailable)
            with pytest.raises(DurableRunPaused, match="daemon removal failure"):
                await bot.resume_run(parent.run_id)
        [blocked] = bot.list_child_runs(parent.run_id)
        assert blocked.run.status == "recovery_required"
        assert lines(child.workspace_path / "effects.txt") == ["effect"]
        [step] = bot.list_child_run_steps(parent.run_id, child.run.run_id)
        assert step.status == "executing"
        await bot.resolve_child_run_step(parent.run_id, child.run.run_id, step.step_id, "Verified one effect")
        assert child_container(child, bot) is None
        await bot.resume_run(parent.run_id)
        assert lines(child.workspace_path / "effects.txt") == ["effect"]
    finally:
        if docker("ps", "-aq", "--filter", f"id={record.container_id}"):
            docker("rm", "-f", "--volumes", record.container_id)
        await bot.agent.close_client()


async def test_parent_pause_cancels_running_docker_child(tmp_path):
    (tmp_path / "host.txt").write_text("host", encoding="utf-8")
    bot = bot_for(tmp_path, long_running=True)
    task = asyncio.create_task(bot.chat("Parent delegates"))
    try:
        async with asyncio.timeout(15):
            while True:
                parents = bot.list_runs()
                children = bot.list_child_runs(parents[0].run_id) if parents else []
                if children:
                    [parent], [child] = parents, children
                    break
                await asyncio.sleep(0.02)
            await wait_for_effect(child)
            record = child_container(child, bot)
            assert bot.pause_run(parent.run_id)
            with pytest.raises(DurableRunPaused):
                await task
        assert docker("ps", "-aq", "--filter", f"id={record.container_id}") == ""
        assert child_container(child, bot) is None
        assert not (child.workspace_path / "escaped-cancel").exists()
        [stopped] = bot.list_child_runs(parent.run_id)
        assert stopped.run.status == "recovery_required"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await bot.agent.close_client()


async def test_replaced_workspace_blocks_before_child_model_or_tool(tmp_path):
    (tmp_path / "host.txt").write_text("host", encoding="utf-8")
    process = spawn(tmp_path, "identity")
    try:
        await wait_for_file(tmp_path / "ready", process)
    finally:
        kill_worker(process)
    bot = bot_for(tmp_path)
    [parent] = bot.list_runs()
    [child] = bot.list_child_runs(parent.run_id)
    moved = child.workspace_path.with_suffix(".original")
    child.workspace_path.rename(moved)
    child.workspace_path.mkdir(mode=0o700)
    try:
        with pytest.raises(DurableRunPaused, match="workspace identity changed"):
            await bot.resume_run(parent.run_id)
        assert lines(tmp_path / "child-model.log") == []
        assert list(child.workspace_path.iterdir()) == []
        child.workspace_path.rmdir()
        moved.rename(child.workspace_path)
        assert (await bot.resume_run(parent.run_id)).assistant_text == "done"
        assert lines(child.workspace_path / "effects.txt") == ["effect"]
    finally:
        await bot.agent.close_client()


async def test_docker_create_without_acknowledgement_fails_closed(tmp_path):
    (tmp_path / "host.txt").write_text("host", encoding="utf-8")
    process = spawn(tmp_path, "create_intent")
    try:
        name = await wait_for_file(tmp_path / "ready", process)
    finally:
        kill_worker(process)
    bot = bot_for(tmp_path)
    try:
        [parent] = bot.list_runs()
        assert docker("ps", "-aq", "--filter", f"name=^/{name}$") == ""
        with pytest.raises(DurableRunPaused, match="create may still be in flight"):
            await bot.resume_run(parent.run_id)
        [blocked] = bot.list_child_runs(parent.run_id)
        assert blocked.run.status == "recovery_required"
        assert not (blocked.workspace_path / "effects.txt").exists()
        assert lines(tmp_path / "child-model.log") == ["start"]
    finally:
        await bot.agent.close_client()


if __name__ == "__main__":
    worker(Path(sys.argv[1]), sys.argv[2], *sys.argv[3:])
