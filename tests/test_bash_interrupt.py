import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.minibot.agent import Agent, UserInterruptedError
from src.minibot.config import BashConfig, Config, LLMConfig, ToolsConfig, TeamsConfig
from src.minibot.config.schema import MemoryConfig, SessionConfig
from src.minibot.sdk import Minibot
from src.minibot.sdk._active_streams import is_active_stream
from src.minibot.subagents.registry import AgentType
from src.minibot.tools.builtin import bash_execution as execution
from src.minibot.tools.function import FunctionTool
from tests.test_bash_execution import process_alive, wait_file, fake_docker


def config(tmp_path, backend="local"):
    return Config(
        workdir=tmp_path, app_home=tmp_path / ".minibot", project_root=tmp_path,
        llm=LLMConfig(base_url="http://localhost:8000/v1", api_key="test", model="test", stream_enabled=False),
        tools=ToolsConfig(bash=BashConfig(backend=backend)),
        teams=TeamsConfig(enabled=False), memory=MemoryConfig(enabled=False),
        session=SessionConfig(enabled=False),
    )


def response(name, args):
    call = SimpleNamespace(id="bash-call", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    message = SimpleNamespace(content="", tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=None)


class BashClient:
    model = "test"

    async def create_message_async(self, **kwargs):
        return response("bash", {"command": "echo $$ > shell; sleep 60"})


def test_disabled_builtin_absent(tmp_path):
    agent = Agent(config=config(tmp_path, "disabled"))
    assert agent.tool_registry.get("bash") is None


@pytest.mark.asyncio
async def test_queued_interrupt_prevents_bash_launch(tmp_path):
    agent = Agent(config=config(tmp_path))
    queue = asyncio.Queue()
    queue.put_nowait(None)
    with pytest.raises(UserInterruptedError):
        await agent._execute_interruptible_tool("bash", {"command": "touch bad"}, queue)
    assert not (tmp_path / "bad").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bash", "Task"])
async def test_custom_sdk_tool_named_like_builtin_unchanged(tmp_path, name):
    agent = Agent(config=config(tmp_path))
    async def custom():
        return "custom"
    agent.tool_registry.register(FunctionTool(custom, name=name))
    queue = asyncio.Queue()
    queue.put_nowait(None)
    assert await agent._execute_interruptible_tool(name, {}, queue) == "custom"
    assert queue.qsize() == 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX shell")
@pytest.mark.parametrize("outer_cancel", [False, True])
async def test_agent_run_loop_interrupt_waits_for_process_cleanup(tmp_path, outer_cancel):
    agent = Agent(config=config(tmp_path))
    agent.silent = True
    agent.client = BashClient()
    queue = asyncio.Queue()
    task = asyncio.create_task(agent.run_loop([{"role": "user", "content": "run"}], interrupt_queue=queue))
    pid = await wait_file(tmp_path / "shell")
    if outer_cancel:
        task.cancel()
        error = asyncio.CancelledError
    else:
        queue.put_nowait(None)
        error = UserInterruptedError
    with pytest.raises(error):
        await asyncio.wait_for(task, 5)
    assert not process_alive(pid)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX shell")
@pytest.mark.parametrize("handler", [False, True])
async def test_enclosing_task_interrupt_reaches_subagent_bash(tmp_path, handler):
    agent = Agent(config=config(tmp_path))
    agent.silent = True
    agent.subagent_executor.client = BashClient()
    args = {"description": "test", "prompt": "run", "agent_type": "code"}
    if handler:
        child = Minibot(config=config(tmp_path))
        child.agent.client = BashClient()
        async def execute_child(*_):
            return (await child.chat("run")).assistant_text
        agent.agent_registry.register(AgentType("test-child", "test", "*", "test", handler=execute_child))
        args["agent_type"] = "test-child"
    queue = asyncio.Queue()
    task = asyncio.create_task(agent._execute_interruptible_tool("Task", args, queue))
    pid = await wait_file(tmp_path / "shell")
    queue.put_nowait(None)
    with pytest.raises(UserInterruptedError):
        await asyncio.wait_for(task, 5)
    assert not process_alive(pid)


@pytest.mark.asyncio
async def test_sdk_cancel_during_bash_waits_for_cleanup(tmp_path):
    bot = Minibot(config=config(tmp_path))
    bot.agent.client = BashClient()
    bot.agent.stream_degraded = True
    bot.agent.silent = True
    async def consume():
        return [event async for event in bot.stream("run")]
    task = asyncio.create_task(consume())
    pid = await wait_file(tmp_path / "shell")
    bot.cancel()
    await asyncio.wait_for(task, 5)
    assert not process_alive(pid)
    assert bot._active_stream is None


def test_sync_iterator_close_finishes_bash_before_residual_task_sweep(tmp_path):
    bot = Minibot(config=config(tmp_path))
    bot.agent.silent = True
    command = "echo $$ > shell; sleep 60"
    pids = []
    async def run(messages, *, interrupt_queue, **kwargs):
        owner = asyncio.create_task(bot.agent._execute_interruptible_tool("bash", {"command": command}, interrupt_queue))
        try:
            pids.append(await wait_file(tmp_path / "shell"))
            await bot._event_router.emit({"type": "system", "message": "running"})
            await owner
        finally:
            if not owner.done():
                owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
    bot.agent.run_loop = run
    stream = bot.stream_sync("run")
    while next(stream).get("message") != "running":
        pass
    stream.close()
    assert pids and not process_alive(pids[0])
    assert bot._active_stream is None


def _close_full_sync_stream_with_cleanup_failure(tmp_path):
    with pytest.MonkeyPatch.context() as monkeypatch:
        started = asyncio.Event()
        calls = fake_docker(monkeypatch, overrides={"rm": execution.ExecutionResult("", "daemon unavailable", 1)})
        fake_process = execution._process

        async def process(*args, **kwargs):
            if args[1] == "start":
                started.set()
                await asyncio.Event().wait()
            return await fake_process(*args, **kwargs)

        monkeypatch.setattr(execution, "_process", process)
        cfg = config(tmp_path, "docker")
        cfg.session.enabled = True
        cfg.session.sessions_dir = str(tmp_path / "sessions")
        bot = Minibot(config=cfg)
        events = []
        persist = bot._handle_stream_persistence_event

        async def tap(event, state):
            events.append(event)
            return await persist(event, state)

        monkeypatch.setattr(bot, "_handle_stream_persistence_event", tap)

        async def run(messages, *, interrupt_queue, **kwargs):
            owner = asyncio.create_task(bot.agent._execute_interruptible_tool("bash", {"command": "sleep 60"}, interrupt_queue))
            try:
                await started.wait()
                for _ in range(257):
                    await bot._event_router.emit({"type": "system", "message": "running"})
                await owner
            finally:
                if not owner.done():
                    owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)

        monkeypatch.setattr(bot.agent, "run_loop", run)
        stream = bot.stream_sync("run")
        assert next(stream)["message"] == "running"
        assert bot._event_router._queue.qsize() == 256
        state = bot._active_stream
        producer = state.producer
        loop = producer.get_loop()
        assert is_active_stream(bot._session_manager, state.session_id, state.run_id)
        stream.close()
        assert producer.done()
        assert not asyncio.all_tasks(loop)
        assert loop.is_closed()
        assert bot._active_stream is None
        assert bot._active_interrupt_queue is None
        assert not is_active_stream(bot._session_manager, state.session_id, state.run_id)
        assert any("Docker cleanup uncertain" in event.get("message", "") for event in events)
        assert any(call[0][1] == "ps" for call in calls)


def test_sync_close_full_queue_preserves_cleanup_diagnostic_and_releases_owner(tmp_path):
    script = (
        "from pathlib import Path; "
        "from tests.test_bash_interrupt import _close_full_sync_stream_with_cleanup_failure; "
        "import sys; _close_full_sync_stream_with_cleanup_failure(Path(sys.argv[1]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding="utf-8", timeout=8,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("enclosing_task", [False, True])
async def test_cleanup_failure_during_interrupt_remains_cancelled_and_visible(tmp_path, monkeypatch, caplog, enclosing_task):
    started = asyncio.Event()
    calls = fake_docker(monkeypatch, overrides={"rm": execution.ExecutionResult("", "daemon unavailable", 1)})
    fake_process = execution._process
    async def process(*args, **kwargs):
        if args[1] == "start":
            started.set()
            await asyncio.sleep(60)
        return await fake_process(*args, **kwargs)
    monkeypatch.setattr(execution, "_process", process)
    agent = Agent(config=config(tmp_path, "docker"))
    events = []
    async def emit(event):
        events.append(event)
    agent._emit = emit
    queue = asyncio.Queue()
    name, args = "bash", {"command": "sleep 60"}
    if enclosing_task:
        agent.subagent_executor.client = BashClient()
        name, args = "Task", {"description": "test", "prompt": "run", "agent_type": "code"}
    task = asyncio.create_task(agent._execute_interruptible_tool(name, args, queue))
    await asyncio.wait_for(started.wait(), 5)
    queue.put_nowait(None)
    with pytest.raises(UserInterruptedError):
        await asyncio.wait_for(task, 5)
    assert "Docker cleanup uncertain for minibot-bash-" in caplog.text
    assert any("Docker cleanup uncertain" in event.get("message", "") for event in events)
    assert any(call[0][1] == "ps" for call in calls)
