"""Inspect real JSONL records without a model service or a trace exporter."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.minibot.agent import Agent, UserInterruptedError
from src.minibot.config import load_config
from src.minibot.config.schema import SessionConfig
from src.minibot.mcp.protocol import MCPTool, MCPToolResult
from src.minibot.mcp.tool_adapter import MCPToolAdapter
from src.minibot.sdk import Minibot
from src.minibot.tools.function import FunctionTool
from src.minibot.tools.registry import ToolRegistry
from src.minibot.traces import RunTrace
from tests.test_agent_streaming import _response
from tests.test_bash_interrupt import config
from tests.test_tool_outcomes import TurnClient, call

SECRET = "secret-credential-never-log-7f392"


def records(directory):
    runs = []
    for path in sorted(directory.glob("*.jsonl")):
        raw = path.read_text(encoding="utf-8")
        assert SECRET not in raw
        run = [json.loads(line) for line in raw.splitlines()]
        assert run[0]["event"] == "run_start"
        assert run[-1]["event"] == "run_end"
        assert [row["sequence"] for row in run] == list(range(1, len(run) + 1))
        assert all(row["run_id"] == path.stem for row in run)
        assert all(row["elapsed_ms"] >= 0 for row in run)
        common = {"version", "event", "run_id", "session_id", "sequence", "elapsed_ms"}
        allowed = {
            "run_start": {"scope", "parent_run_id", "started_at"},
            "run_end": {"status", "duration_ms"},
            "run_waiting": set(), "run_resumed": set(),
            "tool_start": {"invocation_id", "tool_call_id", "tool_name"},
            "tool_end": {"invocation_id", "tool_call_id", "tool_name", "status", "is_error",
                         "duration_ms", "exit_code", "hook_phase"},
        }
        assert all(set(row) <= common | allowed[row["event"]] for row in run)
        starts = [row for row in run if row["event"] == "tool_start"]
        ends = [row for row in run if row["event"] == "tool_end"]
        assert [row["invocation_id"] for row in starts] == [row["invocation_id"] for row in ends]
        assert len({row["invocation_id"] for row in starts}) == len(starts)
        assert all(row["duration_ms"] >= 0 for row in ends)
        runs.append(run)
    return runs


def make_agent(tmp_path):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    agent = Agent(config=cfg)
    agent.silent = True
    agent.status_enabled = False
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "refused", "pre", "post", "error", "invalid", "json", "timeout", "mcp", "unknown"])
async def test_outcomes_pair_and_omit_payloads(tmp_path, monkeypatch, mode):
    agent = make_agent(tmp_path)
    effects = []

    def effect(text: str) -> str:
        effects.append(text)
        if mode == "error":
            raise RuntimeError(SECRET)
        if mode == "timeout":
            raise TimeoutError(SECRET)
        return SECRET

    agent.tool_registry = ToolRegistry()
    agent.tool_registry.register(FunctionTool(effect))
    name = "effect"
    args = json.dumps({"text": SECRET})
    if mode == "invalid":
        args = json.dumps({"wrong": SECRET})
    elif mode == "json":
        args = "{broken" + SECRET
    elif mode == "unknown":
        name = "absent"
    elif mode == "mcp":
        class Client:
            name = "fake"

            async def call_tool(self, **kwargs):
                return MCPToolResult(SECRET, is_error=True)

        adapter = MCPToolAdapter(MCPTool("example", "", {"type": "object"}), Client())
        agent.tool_registry.register(adapter)
        name, args = adapter.name, "{}"

    async def pre(*args):
        if mode == "pre":
            raise RuntimeError(SECRET)
        return mode == "refused", SECRET

    async def post(*args):
        if mode == "post":
            raise RuntimeError(SECRET)

    monkeypatch.setattr(agent.hook_manager, "trigger_pre_tool_call", pre)
    monkeypatch.setattr(agent.hook_manager, "trigger_post_tool_call", post)
    agent.client = TurnClient([call(name, args), call(name, args)])
    await agent.run_loop([{"role": "user", "content": SECRET}])
    run, = records(agent.config.trace_dir)
    ends = [row for row in run if row["event"] == "tool_end"]
    expected = {"pre": "error", "post": "success", "invalid": "invalid_arguments",
                "json": "invalid_arguments", "mcp": "error", "unknown": "unknown_tool"}.get(mode, mode)
    assert [row["status"] for row in ends] == [expected, expected]
    assert all(row["is_error"] == (mode != "success") for row in ends)
    assert [row["tool_call_id"] for row in ends] == ["call-1", "call-1"]
    assert len(effects) == (2 if mode in {"success", "post", "error", "timeout"} else 0)
    if mode in {"pre", "post"}:
        assert all(row["hook_phase"] == mode for row in ends)
    assert run[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_model_failure_records_failure_even_when_sdk_returns_text(tmp_path):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    bot = Minibot(config=cfg)

    class Fail:
        async def create_message_async(self, **kwargs):
            raise RuntimeError(SECRET)

    bot.agent.client = Fail()
    result = await bot.chat(SECRET)
    assert SECRET in result.assistant_text
    run, = records(cfg.trace_dir)
    assert run[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_disabled_has_no_files_and_chat_text_is_unchanged(tmp_path):
    cfg = config(tmp_path)
    bot = Minibot(config=cfg)
    bot.agent.client = TurnClient([])
    assert (await bot.chat("hello")).assistant_text == "done"
    assert not list(tmp_path.rglob("*.jsonl"))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pre", "post"])
async def test_duration_includes_hook_and_cancellation_waits_for_finalizer(tmp_path, monkeypatch, phase):
    agent = make_agent(tmp_path)
    entered, release, finalized = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def hook(*args):
        try:
            entered.set()
            await release.wait()
            return False, None
        finally:
            path, = agent.config.trace_dir.glob("*.jsonl")
            assert "run_end" not in path.read_text(encoding="utf-8")
            await asyncio.sleep(0)
            finalized.set()

    monkeypatch.setattr(agent.hook_manager, f"trigger_{phase}_tool_call", hook)
    agent.client = TurnClient([call("absent")])
    task = asyncio.create_task(agent.run_loop([{"role": "user", "content": "run"}]))
    await asyncio.wait_for(entered.wait(), 3)
    path, = agent.config.trace_dir.glob("*.jsonl")
    before = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in before] == ["run_start", "tool_start"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finalized.is_set()
    run, = records(agent.config.trace_dir)
    assert run[-1]["status"] == "interrupted"
    assert run[-2]["status"] == "interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_cancel", [False, True])
async def test_bash_terminal_record_follows_real_process_cleanup(tmp_path, outer_cancel):
    import os
    from tests.test_bash_execution import process_alive, wait_file
    from tests.test_bash_interrupt import BashClient

    if os.name != "posix":
        pytest.skip("POSIX process ownership")
    agent = make_agent(tmp_path)
    agent.client = BashClient()
    interrupt = asyncio.Queue()
    task = asyncio.create_task(agent.run_loop([{"role": "user", "content": "run"}], interrupt_queue=interrupt))
    pid = await wait_file(tmp_path / "shell")
    if outer_cancel:
        task.cancel()
    else:
        interrupt.put_nowait(None)
    with pytest.raises(asyncio.CancelledError if outer_cancel else UserInterruptedError):
        await task
    assert not process_alive(pid)
    run, = records(agent.config.trace_dir)
    assert run[-1]["status"] == "interrupted"
    assert run[-2]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_concurrent_sessions_and_repeated_round_ids(tmp_path):
    agents = [make_agent(tmp_path), make_agent(tmp_path)]

    class Repeated:
        def __init__(self):
            self.round = 0

        async def create_message_async(self, **kwargs):
            self.round += 1
            await asyncio.sleep(0)
            if self.round <= 2:
                return _response(content=SECRET, finish_reason="tool_calls", tool_calls=[call("absent")])
            return _response(content=SECRET)

    for agent in agents:
        agent.client = Repeated()
    await asyncio.gather(*(agent.run_loop([{"role": "user", "content": SECRET}]) for agent in agents))
    runs = records(tmp_path / "traces")
    assert len(runs) == 2
    assert {run[0]["session_id"] for run in runs} == {agent.session_id for agent in agents}
    assert all(len([row for row in run if row["event"] == "tool_end"]) == 2 for run in runs)


@pytest.mark.asyncio
async def test_live_question_wait_and_answer_share_run(tmp_path):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    bot = Minibot(config=cfg)
    bot.agent.client = TurnClient([call("askuserquestion", json.dumps({"prompt": SECRET}))])
    stream = bot.stream("run")
    async for event in stream:
        if event["type"] == "ask_user_question":
            await bot.submit_user_answer(question_id="call-1", answer_text=SECRET)
    run, = records(cfg.trace_dir)
    assert [row["event"] for row in run] == [
        "run_start", "tool_start", "tool_end", "run_waiting", "run_resumed", "run_end",
    ]
    assert run[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_persisted_question_restart_starts_linked_segment(tmp_path):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    cfg.session = SessionConfig(sessions_dir=str(tmp_path / "sessions"))
    bot = Minibot(config=cfg)
    bot.agent.client = TurnClient([call("askuserquestion", json.dumps({"prompt": SECRET}))])
    async for _ in bot.stream("run", stop_on_ask_user_question=True):
        pass
    first, = records(cfg.trace_dir)
    assert first[-1]["status"] == "waiting_for_user"
    state = bot._session_manager.load_runtime_state(bot.session_id)
    assert state["trace_run_id"] == first[0]["run_id"]
    restarted = Minibot(config=cfg, session_id=bot.session_id)
    restarted.agent.client = TurnClient([])
    async for _ in restarted.stream_existing_messages():
        pass
    runs = records(cfg.trace_dir)
    resumed, = [run for run in runs if run[0]["parent_run_id"]]
    assert resumed[0]["parent_run_id"] == first[0]["run_id"]
    assert resumed[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_detached_consumer_does_not_end_trace(tmp_path):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    bot = Minibot(config=cfg)
    entered, release = asyncio.Event(), asyncio.Event()

    async def owned() -> str:
        entered.set()
        await release.wait()
        return SECRET

    bot.register_tool(owned)
    bot.agent.client = TurnClient([call("owned")])
    stream = bot.stream("run")
    await stream.__anext__()
    await asyncio.wait_for(entered.wait(), 3)
    producer = bot._active_stream.producer
    await stream.aclose()
    assert not producer.done()
    path, = cfg.trace_dir.glob("*.jsonl")
    assert "run_end" not in path.read_text(encoding="utf-8")
    release.set()
    await asyncio.wait_for(producer, 3)
    run, = records(cfg.trace_dir)
    assert run[-1]["status"] == "completed"
    assert bot.messages[-1]["content"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["open", "write", "flush", "close"])
async def test_trace_io_failure_keeps_generation_and_hides_error(tmp_path, monkeypatch, caplog, failure):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    original = Path.open

    class BrokenFile:
        def write(self, value):
            if failure == "write":
                raise OSError(SECRET)

        def flush(self):
            if failure == "flush":
                raise OSError(SECRET)

        def close(self):
            if failure == "close":
                raise OSError(SECRET)

    def open_file(path, *args, **kwargs):
        if path.suffix == ".jsonl":
            assert kwargs.get("encoding") == "utf-8"
            assert args == ("x",)
            if failure == "open":
                raise OSError(SECRET)
            return BrokenFile()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    bot = Minibot(config=cfg)
    bot.agent.client = TurnClient([])
    assert (await bot.chat("hello")).assistant_text == "done"
    assert "recording disabled" in caplog.text
    assert SECRET not in caplog.text


def test_trace_and_config_text_io_explicit_utf8(tmp_path, monkeypatch):
    import builtins

    original = Path.open
    builtin_open = builtins.open

    def checked(path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked)

    def checked_builtin(path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return builtin_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", checked_builtin)
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config/default.yaml").write_text("trace_dir: traces\n", encoding="utf-8")
    cfg = load_config(workdir=tmp_path, app_home=home, project_root=tmp_path)
    assert cfg.trace_dir == home / "traces"
    trace = RunTrace(cfg.trace_dir, "中文-session")
    trace.close("completed")
    run, = records(cfg.trace_dir)
    assert run[0]["session_id"] == "中文-session"


@pytest.mark.asyncio
async def test_task_records_parent_only_without_duplicate_child_calls(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    monkeypatch.setattr("src.minibot.subagents.executor.rich_enabled", lambda: False)
    effects = []

    def child_effect() -> str:
        effects.append(True)
        return SECRET

    agent.tool_registry.register(FunctionTool(child_effect))
    agent.client = TurnClient([call("Task", json.dumps({
        "description": SECRET, "prompt": SECRET, "agent_type": "code",
    }))])
    agent.subagent_executor.client = TurnClient([call("child_effect")])
    await agent.run_loop([{"role": "user", "content": "run"}])
    assert effects == [True]
    run, = records(agent.config.trace_dir)
    assert run[0]["scope"] == "agent_tools"
    assert [row["tool_name"] for row in run if row["event"] == "tool_start"] == ["Task"]


@pytest.mark.asyncio
async def test_sink_backpressure_and_hook_time_are_inside_duration(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    clock = [0.0]
    monkeypatch.setattr("src.minibot.traces.time", SimpleNamespace(monotonic=lambda: clock[0]))
    entered, release = asyncio.Event(), asyncio.Event()

    async def post(*args):
        entered.set()
        await release.wait()
        clock[0] += 7

    class Sink:
        async def emit(self, event):
            if event["type"] == "tool_call":
                clock[0] += 3

    monkeypatch.setattr(agent.hook_manager, "trigger_post_tool_call", post)
    agent.event_sink = Sink()
    agent.client = TurnClient([call("absent")])
    task = asyncio.create_task(agent.run_loop([{"role": "user", "content": "run"}]))
    await asyncio.wait_for(entered.wait(), 3)
    path, = agent.config.trace_dir.glob("*.jsonl")
    assert "tool_end" not in path.read_text(encoding="utf-8")
    release.set()
    await task
    run, = records(agent.config.trace_dir)
    assert run[-2]["event"] == "tool_end"
    assert run[-2]["duration_ms"] == 10000


@pytest.mark.asyncio
async def test_disk_failure_does_not_swallow_tool_cancellation(tmp_path, caplog):
    agent = make_agent(tmp_path)
    agent.config.trace_dir = tmp_path / "file"
    agent.config.trace_dir.write_text(SECRET, encoding="utf-8")
    entered, finalized = asyncio.Event(), asyncio.Event()

    async def owned() -> str:
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            finalized.set()

    agent.tool_registry.register(FunctionTool(owned))
    agent.client = TurnClient([call("owned")])
    task = asyncio.create_task(agent.run_loop([{"role": "user", "content": "run"}]))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finalized.is_set()
    assert "recording disabled" in caplog.text
    assert SECRET not in caplog.text


def test_sync_chat_and_stream_keep_distinct_runs(tmp_path):
    cfg = config(tmp_path)
    cfg.trace_dir = tmp_path / "traces"
    bot = Minibot(config=cfg)
    bot.agent.client = TurnClient([])
    assert bot.chat_sync("hello").assistant_text == "done"
    assert any(event["type"] == "assistant_end" for event in bot.stream_sync("hello"))
    runs = records(cfg.trace_dir)
    assert len(runs) == 2
    assert all(run[-1]["status"] == "completed" for run in runs)


@pytest.mark.asyncio
async def test_bash_timeout_and_exit_code_survive_jsonl(tmp_path):
    from src.minibot.tools.builtin.bash import BashTool

    agent = make_agent(tmp_path)
    agent.tool_registry.register(BashTool(tmp_path, timeout=0.1))
    agent.client = TurnClient([
        call("bash", json.dumps({"command": "sleep 60"})),
        call("bash", json.dumps({"command": "printf payload; exit 7"})),
    ])
    await agent.run_loop([{"role": "user", "content": "run"}])
    run, = records(agent.config.trace_dir)
    ends = [row for row in run if row["event"] == "tool_end"]
    assert [row["status"] for row in ends] == ["timeout", "error"]
    assert ends[1]["exit_code"] == 7
    assert run[-1]["status"] == "completed"
