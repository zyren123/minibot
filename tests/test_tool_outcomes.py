"""Tool status survives dispatch, hooks, model turns and persistence."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.minibot.config.schema import BashConfig, HookDefinition, HooksConfig
from src.minibot.events import AsyncQueueEventSink
from src.minibot.hooks.events import HookResult
from src.minibot.hooks.manager import HookManager
from src.minibot.mcp.protocol import MCPTool, MCPToolResult
from src.minibot.mcp.tool_adapter import MCPToolAdapter
from src.minibot.server.models import MessageResponse
from src.minibot.session.store import SessionStore
from src.minibot.subagents.executor import SubagentExecutor
from src.minibot.subagents.registry import AgentRegistry, AgentType
from src.minibot.tools.base import BaseTool
from src.minibot.tools.builtin.ask_user import AskUserQuestionTool
from src.minibot.tools.builtin.bash import BashTool
from src.minibot.tools.builtin.file import EditFileTool, ReadFileTool, WriteFileTool
from src.minibot.tools.builtin.todo import TodoManager, TodoWriteTool
from src.minibot.tools.execution import execute_with_hooks
from src.minibot.tools.function import FunctionTool
from src.minibot.tools.meta.skill import SkillTool
from src.minibot.tools.meta.task import TaskTool
from src.minibot.tools.meta.team import TeamWaitTool
from src.minibot.tools.registry import ToolRegistry
from src.minibot.tools.result import ToolOutcome
from tests.test_agent_streaming import _make_agent, _response
from tests.test_llm_client_async import _install_fake_openai_clients
from src.minibot.core.client import LLMClient


class TurnClient:
    model = "test-model"

    def __init__(self, calls):
        self.calls = calls
        self.requests = []

    async def create_message_async(self, **kwargs):
        self.requests.append([dict(message) for message in kwargs["messages"]])
        if len(self.requests) == 1:
            return _response(content="", finish_reason="tool_calls", tool_calls=self.calls)
        return _response(content="done")


def call(name, args="{}", id="call-1"):
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=args))


def hooks(tmp_path):
    return HookManager(HooksConfig(), tmp_path)


@pytest.mark.asyncio
async def test_registry_preserves_text_and_classifies_boundaries(tmp_path):
    registry = ToolRegistry()
    effects = []

    def echo(text: str) -> str:
        effects.append(text)
        return text

    def fail() -> str:
        raise TypeError("inside tool")

    def timeout() -> str:
        raise TimeoutError("deadline")

    registry.register(FunctionTool(echo))
    registry.register(FunctionTool(fail))
    registry.register(FunctionTool(timeout))
    success = await registry.execute_result("echo", {"text": "Error: valid payload"})
    assert not success.is_error
    assert success.text == "Error: valid payload"
    assert await registry.execute("echo", {"text": "legacy"}) == "legacy"
    assert (await registry.execute_result("absent", {})).status == "unknown_tool"
    for args in [{}, {"text": "never", "extra": 1}, []]:
        assert (await registry.execute_result("echo", args)).status == "invalid_arguments"
    assert effects == ["Error: valid payload", "legacy"]
    failure = await registry.execute_result("fail", {})
    assert failure.status == "error" and failure.text == "Error: inside tool"
    assert (await registry.execute_result("timeout", {})).status == "timeout"

    class Legacy(BaseTool):
        name = "legacy"
        description = "legacy custom integration"
        input_schema = {"type": "object"}

        async def execute(self, value):
            return value

    registry.register(Legacy())
    assert (await registry.execute_result("legacy", {"value": "Error: payload"})).status == "success"
    assert (await registry.execute_result("legacy", {"value": "x", "extra": 1})).status == "invalid_arguments"


@pytest.mark.asyncio
async def test_mcp_error_flag_is_preserved_without_prefix_inference():
    class MCPClient:
        name = "fake"

        async def call_tool(self, **kwargs):
            return MCPToolResult("normal-looking output", is_error=True)

    tool = MCPToolAdapter(MCPTool("example", "", {"type": "object"}), MCPClient())
    assert await tool.execute() == "normal-looking output"
    outcome = await tool.execute_result()
    assert outcome.is_error and outcome.text == "normal-looking output"


@pytest.mark.asyncio
async def test_hooks_refuse_before_effect_and_record_post_failure(tmp_path, monkeypatch):
    registry = ToolRegistry()
    effects = []

    def effect() -> str:
        effects.append("executed")
        return "original result"

    registry.register(FunctionTool(effect))
    manager = hooks(tmp_path)

    async def blocked(*args):
        return True, "policy"

    monkeypatch.setattr(manager, "trigger_pre_tool_call", blocked)
    result = await execute_with_hooks(registry, manager, "effect", {})
    assert result.status == "refused" and result.text == "Blocked by hook: policy"
    assert effects == []

    async def allow(*args):
        return False, None

    async def post(name, args, text):
        assert text == "original result"
        raise RuntimeError("post failed")

    monkeypatch.setattr(manager, "trigger_pre_tool_call", allow)
    monkeypatch.setattr(manager, "trigger_post_tool_call", post)
    result = await execute_with_hooks(registry, manager, "effect", {})
    assert effects == ["executed"]
    assert result.text == "original result" and result.status == "success"
    assert result.is_error and result.hook_phase == "post" and result.hook_error == "post failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pre", "post"])
async def test_hook_executor_failure_results_are_observed(tmp_path, monkeypatch, phase):
    config = HooksConfig(enabled=True, hooks=[HookDefinition(event=f"{phase}_tool_call", handler="missing:handler")])
    manager = HookManager(config, tmp_path)
    registry = ToolRegistry()
    effects = []

    def effect() -> str:
        effects.append(True)
        return "ok"

    registry.register(FunctionTool(effect))

    async def failure(**kwargs):
        return HookResult(success=False, error="hook unavailable")

    monkeypatch.setattr(manager.executor, "execute", failure)
    outcome = await execute_with_hooks(registry, manager, "effect", {})
    assert outcome.is_error and outcome.hook_phase == phase
    assert outcome.hook_error == "hook unavailable"
    assert effects == ([] if phase == "pre" else [True])
    assert outcome.status == ("error" if phase == "pre" else "success")


@pytest.mark.asyncio
async def test_builtin_error_sources_and_bash_statuses(tmp_path, monkeypatch):
    assert (await BashTool(tmp_path).execute_result("printf 'Error: payload'")).status == "success"
    result = await BashTool(tmp_path).execute_result("printf payload; exit 7")
    assert result.status == "error" and result.exit_code == 7
    assert result.text == "Error: Command exited with code 7\npayload"
    assert (await BashTool(tmp_path, config=BashConfig(backend="disabled")).execute_result("true")).status == "refused"
    assert (await BashTool(tmp_path).execute_result("sudo true")).status == "refused"
    assert (await BashTool(tmp_path, timeout=0.01).execute_result("sleep 60")).status == "timeout"
    assert (await ReadFileTool(tmp_path).execute_result("absent")).status == "error"
    assert (await EditFileTool(tmp_path).execute_result("absent", "a", "b")).status == "error"
    assert (await TodoWriteTool(TodoManager()).execute_result([{}])).status == "error"
    assert (await AskUserQuestionTool().execute_result(prompt="")).status == "invalid_arguments"
    loader = SimpleNamespace(get_skill_content=lambda _: None, list_skills=lambda: [])
    assert (await SkillTool(loader).execute_result("absent")).status == "error"
    assert (await TeamWaitTool(None, default_timeout_sec=1).execute_result()).status == "error"
    task = TaskTool(AgentRegistry(), None)
    assert (await task.execute_result("test", "prompt", "absent")).status == "invalid_arguments"


@pytest.mark.asyncio
async def test_file_tools_explicit_utf8_and_error_looking_contents(tmp_path, monkeypatch):
    read, write = Path.read_text, Path.write_text

    def read_utf8(path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return read(path, *args, **kwargs)

    def write_utf8(path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_utf8)
    monkeypatch.setattr(Path, "write_text", write_utf8)
    assert not (await WriteFileTool(tmp_path).execute_result("test.md", "Error: 中文")).is_error
    result = await ReadFileTool(tmp_path).execute_result("test.md")
    assert result.text == "Error: 中文" and not result.is_error
    assert not (await EditFileTool(tmp_path).execute_result("test.md", "中文", "正常")).is_error


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["main", "subagent"])
async def test_main_and_subagent_keep_outcomes_and_model_text(tmp_path, monkeypatch, target):
    client = TurnClient([call("absent"), call("echo", '{"text":"Error: payload"}', "call-2"), call("echo", "{bad", "call-3")])
    registry = ToolRegistry()

    def echo(text: str) -> str:
        return text

    registry.register(FunctionTool(echo))
    if target == "main":
        agent = _make_agent(tmp_path)
        agent.config.llm.stream_enabled = False
        agent.client = client
        agent.tool_registry = registry
        queue = asyncio.Queue()
        agent.event_sink = AsyncQueueEventSink(queue)
        history = await agent.run_loop([{"role": "user", "content": "run"}])
        emitted = []
        while not queue.empty():
            event = queue.get_nowait()
            if event["type"] == "tool_result":
                emitted.append(event)
        assert [event["tool_status"] for event in emitted] == ["unknown_tool", "success", "invalid_arguments"]
    else:
        monkeypatch.setattr("src.minibot.subagents.executor.rich_enabled", lambda: False)
        executor = SubagentExecutor(client, registry, AgentRegistry(), hooks(tmp_path), tmp_path)
        assert await executor.execute("test", "run", "code") == "done"
        history = client.requests[-1]
    results = [message for message in history if message["role"] == "tool"]
    missing_status = "unknown_tool" if target == "main" else "refused"
    assert [message["tool_status"] for message in results] == [missing_status, "success", "invalid_arguments"]
    assert [message["is_error"] for message in results] == [True, False, True]
    assert "absent" in results[0]["content"]
    assert results[1]["content"] == "Error: payload"


@pytest.mark.asyncio
async def test_subagent_enforces_advertised_tool_list_before_hooks(tmp_path, monkeypatch):
    client = TurnClient([call("allowed"), call("unlisted", id="call-2")])
    agent_registry = AgentRegistry()
    agent_registry.register(AgentType("restricted", "Use one tool", ["allowed"], "Use the allowed tool."))
    tool_registry = ToolRegistry()
    manager = hooks(tmp_path)
    effects = []

    def allowed() -> str:
        effects.append("allowed tool")
        return "allowed result"

    def unlisted() -> str:
        effects.append("unlisted tool")
        return "unexpected result"

    async def pre_hook(name, _args):
        effects.append(f"pre {name}")
        return False, None

    async def post_hook(name, _args, _output):
        effects.append(f"post {name}")

    tool_registry.register(FunctionTool(allowed))
    tool_registry.register(FunctionTool(unlisted))
    monkeypatch.setattr(manager, "trigger_pre_tool_call", pre_hook)
    monkeypatch.setattr(manager, "trigger_post_tool_call", post_hook)
    executor = SubagentExecutor(client, tool_registry, agent_registry, manager, tmp_path)

    assert await executor.execute("test", "run", "restricted") == "done"

    results = [message for message in client.requests[-1] if message["role"] == "tool"]
    assert [message["tool_status"] for message in results] == ["success", "refused"]
    assert results[0]["content"] == "allowed result"
    assert results[1]["is_error"] is True
    assert effects == ["pre allowed", "allowed tool", "post allowed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args,blocked", [('{}', False), ('{"prompt":"question"}', True)])
async def test_failed_ask_user_never_enters_pause(tmp_path, monkeypatch, args, blocked):
    agent = _make_agent(tmp_path)
    agent.config.llm.stream_enabled = False
    agent.client = TurnClient([call("askuserquestion", args)])
    if blocked:
        async def block(*args):
            return True, "policy"
        monkeypatch.setattr(agent.hook_manager, "trigger_pre_tool_call", block)
    history = await agent.run_loop([{"role": "user", "content": "run"}])
    results = [message for message in history if message["role"] == "tool"]
    assert results[0]["tool_status"] == ("refused" if blocked else "invalid_arguments")
    assert not any(message.get("question_pending") for message in history)


@pytest.mark.asyncio
async def test_repaired_ask_user_json_still_pauses_and_resumes(tmp_path):
    agent = _make_agent(tmp_path)
    agent.config.llm.stream_enabled = False
    agent.client = TurnClient([call("askuserquestion", '{"prompt":"question"} trailing')])
    asked = []

    async def answer(question):
        asked.append(question["prompt"])
        return {"answer_text": "answer"}

    agent.ask_user_handler = answer
    history = await agent.run_loop([{"role": "user", "content": "run"}])
    assert asked == ["question"]
    assert any(message.get("answer_to_question_id") == "call-1" for message in history)
    assert not any(message.get("question_pending") for message in history)


def test_outcome_metadata_survives_utf8_persistence_and_response(tmp_path, monkeypatch):
    original_open, original_read = Path.open, Path.read_text

    def open_utf8(path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return original_open(path, *args, **kwargs)

    def read_utf8(path, *args, **kwargs):
        assert kwargs.get("encoding") == "utf-8"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_utf8)
    monkeypatch.setattr(Path, "read_text", read_utf8)
    outcome = ToolOutcome("中文", "error", exit_code=7, hook_error="post failed", hook_phase="post")
    message = {"role": "tool", "tool_call_id": "call-1", "content": outcome.text, **outcome.metadata()}
    store = SessionStore(tmp_path / "sessions")
    store.create("test")
    store.append("test", message)
    loaded = store.load("test")[0]
    response = MessageResponse.model_validate(loaded).model_dump()
    assert response["content"] == "中文"
    for key, value in outcome.metadata().items():
        assert loaded[key] == value and response[key] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["create_message", "create_message_async", "create_message_stream_async"])
async def test_outcome_metadata_never_reaches_provider_payload(monkeypatch, method):
    records = _install_fake_openai_clients(monkeypatch)
    client = LLMClient(base_url="http://localhost:8000/v1", api_key="test", model="test")
    original = {"role": "tool", "tool_call_id": "call-1", "content": "payload", **ToolOutcome("payload", "error", 7).metadata()}
    if method == "create_message":
        client.create_message(messages=[original], system="")
        kwargs = records["sync_create_kwargs"]
    else:
        await getattr(client, method)(messages=[original], system="")
        kwargs = records["async_create_kwargs"]
    assert kwargs["messages"] == [{"role": "tool", "tool_call_id": "call-1", "content": "payload"}]
    assert original["tool_status"] == "error"
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["main", "subagent"])
@pytest.mark.parametrize("phase", ["pre", "post"])
async def test_main_subagent_hook_outcomes_preserve_effect_history(tmp_path, monkeypatch, target, phase):
    client = TurnClient([call("effect")])
    manager = HookManager(
        HooksConfig(enabled=True, hooks=[HookDefinition(event=f"{phase}_tool_call", handler="missing:handler")]),
        tmp_path,
    )
    effects = []
    registry = ToolRegistry()

    def effect() -> str:
        effects.append("once")
        return "original text"

    registry.register(FunctionTool(effect))

    async def fail(**kwargs):
        return HookResult(success=False, error="hook failed")

    monkeypatch.setattr(manager.executor, "execute", fail)
    if target == "main":
        agent = _make_agent(tmp_path)
        agent.config.llm.stream_enabled = False
        agent.client, agent.tool_registry, agent.hook_manager = client, registry, manager
        history = await agent.run_loop([{"role": "user", "content": "run"}])
    else:
        monkeypatch.setattr("src.minibot.subagents.executor.rich_enabled", lambda: False)
        executor = SubagentExecutor(client, registry, AgentRegistry(), manager, tmp_path)
        await executor.execute("test", "run", "code")
        history = client.requests[-1]
    result = next(message for message in history if message["role"] == "tool")
    assert result["tool_hook_phase"] == phase and result["tool_hook_error"] == "hook failed"
    assert result["is_error"] is True
    assert effects == ([] if phase == "pre" else ["once"])
    if phase == "post":
        assert result["tool_status"] == "success" and result["content"] == "original text"


@pytest.mark.asyncio
async def test_registry_cancellation_is_raised_after_tool_finalizer(tmp_path):
    registry = ToolRegistry()
    entered, finalized = asyncio.Event(), asyncio.Event()

    async def owned() -> str:
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            finalized.set()

    registry.register(FunctionTool(owned))
    run = asyncio.create_task(execute_with_hooks(registry, hooks(tmp_path), "owned", {}))
    await entered.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert finalized.is_set()


@pytest.mark.asyncio
async def test_structured_bash_cancellation_waits_for_process_cleanup(tmp_path):
    import os
    from tests.test_bash_execution import process_alive, wait_file

    if os.name != "posix":
        pytest.skip("POSIX process groups")
    registry = ToolRegistry()
    registry.register(BashTool(tmp_path))
    run = asyncio.create_task(registry.execute_result("bash", {
        "command": "echo $$ > shell; sleep 60 & echo $! > child; wait",
    }))
    shell, child = await wait_file(tmp_path / "shell"), await wait_file(tmp_path / "child")
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert not process_alive(shell) and not process_alive(child)
