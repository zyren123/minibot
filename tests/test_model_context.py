"""Verify the context presented to a model at the Agent and SDK boundaries."""

import json
from types import SimpleNamespace

import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import RuntimeBudgetConfig, SessionConfig
from src.minibot.core.model_context import ContextWindowExceeded, estimate_payload_tokens
from src.minibot.core.runtime_budget import current_budget
from src.minibot.sdk import Minibot
from src.minibot.subagents.registry import AgentType
from src.minibot.tools.function import FunctionTool
from tests.test_agent_streaming import _response
from tests.test_bash_interrupt import config
from tests.test_run_traces import records
from tests.test_task_regression import ScriptedProvider, tool_turn
from tests.test_tool_outcomes import call


class RecordingProvider(ScriptedProvider):
    def __init__(self, *responses):
        super().__init__(*responses)
        self.requests = []

    async def create_message_async(self, **kwargs):
        self.requests.append(kwargs)
        return await super().create_message_async(**kwargs)


def make_agent(tmp_path):
    agent = Agent(config(tmp_path, backend="disabled"))
    agent.silent = True
    return agent


def input_tokens(request):
    return estimate_payload_tokens((request["system"], request["messages"], request["tools"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_agent_refuses_irreducible_context_before_dispatch_or_budget_charge(tmp_path, stream):
    agent = make_agent(tmp_path)
    agent.config.llm.max_context_tokens = 4000
    agent.config.trace_dir = tmp_path / "traces"
    agent.set_stream_enabled(stream)
    provider = RecordingProvider()
    agent.client = provider
    observed = []

    class Sink:
        async def emit(self, event):
            observed.append((event, current_budget.get().llm_calls))

    agent.event_sink = Sink()
    history = [{"role": "user", "content": "Z" * 20000}]
    with pytest.raises(ContextWindowExceeded, match="context_window_exceeded"):
        await agent.run_loop(history)

    assert not provider.requests
    assert not agent.stream_degraded
    event, calls = observed[-1]
    assert event["data"]["code"] == "context_window_exceeded"
    assert calls == 0
    run, = records(agent.config.trace_dir)
    assert run[-1]["status"] == "context_window_exceeded"


@pytest.mark.asyncio
async def test_agent_retains_current_question_and_tool_group_when_trimming_old_turns(tmp_path):
    agent = make_agent(tmp_path)
    agent.config.llm.max_tokens = 80
    agent.config.llm.max_context_tokens = 5000
    agent.client = RecordingProvider(_response(content="done"))
    instructions = {"role": "developer", "content": "Keep the tool IDs paired."}
    assistant_tool = {"role": "assistant", "tool_calls": [
        {"id": "new-tool", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
    ]}
    latest = [
        {"role": "user", "content": "Start the current task."},
        {"role": "assistant", "content": "Which mode?", "question_id": "question-1"},
        {"role": "user", "content": "Quick", "answer_to_question_id": "question-1"},
        assistant_tool,
        {"role": "tool", "tool_call_id": "new-tool", "content": "tool result"},
    ]
    prior = [
        instructions,
        {"role": "assistant", "is_compaction": True, "content": "old summary " * 3000},
        {"role": "user", "content": "old " * 5000},
        {"role": "assistant", "tool_calls": [
            {"id": "old-tool", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "old-tool", "content": "old result"},
        *latest,
    ]
    history = list(prior)
    await agent.run_loop(history)

    request, = agent.client.requests
    assert request["messages"] == [instructions, *latest]
    assert request["max_tokens"] == 80
    assert input_tokens(request) + request["max_tokens"] <= agent.config.llm.max_context_tokens
    assert history[:-1] == prior


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_output", [6, 500])
async def test_agent_reserves_the_effective_completion_headroom(tmp_path, remaining_output):
    agent = make_agent(tmp_path)
    agent.config.llm.max_tokens = 1000
    agent.config.runtime_budget = RuntimeBudgetConfig(max_output_tokens=remaining_output)
    agent.client = RecordingProvider(_response(content="ok"))
    history = [
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old response"},
        {"role": "user", "content": "new user"},
    ]
    tools = agent.tool_registry.get_definitions()
    agent.config.llm.max_context_tokens = estimate_payload_tokens((agent.system_prompt, history, tools)) + 8
    await agent.run_loop(history)

    request, = agent.client.requests
    if remaining_output == 6:
        assert request["messages"] == history[:-1]
        assert request["max_tokens"] == 6
    else:
        assert request["messages"] == history[2:-1]
        assert input_tokens(request) + request["max_tokens"] == agent.config.llm.max_context_tokens
        assert 0 < request["max_tokens"] < remaining_output


@pytest.mark.asyncio
async def test_stream_and_fallback_both_cap_legacy_tool_output(tmp_path):
    agent = make_agent(tmp_path)
    agent.config.llm.max_tool_output_chars = 64
    agent.set_stream_enabled(True)
    attempts = []

    class Fallback(RecordingProvider):
        async def create_message_stream_async(self, **kwargs):
            attempts.append(kwargs)
            raise ValueError("stream unavailable")

    provider = Fallback(_response(content="done"))
    agent.client = provider
    result = "输出" * 300
    messages = [
        {"role": "user", "content": "Run the tool."},
        {"role": "assistant", "tool_calls": [
            {"id": "legacy", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "legacy", "content": result},
    ]
    await agent.run_loop(messages)

    stream, = attempts
    plain, = provider.requests
    assert stream["messages"] == plain["messages"]
    assert plain["messages"][-1]["tool_call_id"] == "legacy"
    visible = plain["messages"][-1]["content"]
    assert visible.endswith("[Tool output truncated]")
    assert len(visible) == 64
    assert messages[2]["content"] == result


@pytest.mark.asyncio
async def test_sdk_stream_caps_new_tool_output_only_for_the_provider(tmp_path):
    cfg = config(tmp_path, backend="disabled")
    cfg.llm.max_tool_output_chars = 80
    cfg.session = SessionConfig(enabled=True, sessions_dir=str(tmp_path / "sessions"))
    bot = Minibot(config=cfg, session_id="context-session")
    raw = "原始工具输出" * 400

    def report() -> str:
        return raw

    bot.agent.tool_registry.register(FunctionTool(report))
    provider = RecordingProvider(tool_turn(call("report")), _response(content="done"))
    bot.agent.client = provider
    events = [event async for event in bot.stream("Run the report.")]

    tool = provider.requests[-1]["messages"][-1]
    assert tool["content"].endswith("[Tool output truncated]")
    assert len(tool["content"]) == 80
    assert next(event["tool_output"] for event in events if event["type"] == "tool_result") == raw
    history = bot._session_manager.load("context-session")
    assert next(message["content"] for message in history if message["role"] == "tool") == raw


@pytest.mark.asyncio
async def test_sdk_context_stop_clears_streaming_state_and_retains_tool_results(tmp_path):
    cfg = config(tmp_path, backend="disabled")
    cfg.llm.max_context_tokens = 5000
    cfg.session = SessionConfig(enabled=True, sessions_dir=str(tmp_path / "sessions"))
    cfg.trace_dir = tmp_path / "traces"
    bot = Minibot(config=cfg, session_id="context-stop")
    bot.agent.silent = True

    def report() -> str:
        return "saved"

    bot.agent.tool_registry.register(FunctionTool(report))
    response = _response(content="x" * 22000, tool_calls=[call("report")], finish_reason="tool_calls")
    provider = RecordingProvider(response)
    bot.agent.client = provider

    events = [event async for event in bot.stream("Run the report.")]
    assert len(provider.requests) == 1
    assert any(event.get("data", {}).get("code") == "context_window_exceeded" for event in events)
    assert bot._session_manager.load_runtime_state("context-stop") is None
    history = bot._session_manager.load("context-stop")
    assert next(message["content"] for message in history if message["role"] == "tool") == "saved"
    assert history[-1]["content"].startswith("[Error: context_window_exceeded:")
    run, = records(cfg.trace_dir)
    assert run[-1]["status"] == "context_window_exceeded"


@pytest.mark.asyncio
async def test_builtin_task_caps_child_tool_output_and_uses_configured_output_limit(tmp_path):
    agent = make_agent(tmp_path)
    agent.config.llm.max_tokens = 47
    agent.config.llm.max_tool_output_chars = 96
    raw = "subagent output " * 50

    def report() -> str:
        return raw

    agent.tool_registry.register(FunctionTool(report))
    agent.client = RecordingProvider(tool_turn(call("Task", json.dumps({
        "description": "Verify context", "prompt": "Run the report.", "agent_type": "code",
    }))), _response(content="done"))
    child = RecordingProvider(tool_turn(call("report")), _response(content="child done"))
    agent.subagent_executor.client = child
    await agent.run_loop([{"role": "user", "content": "Delegate a report."}])

    assert [request["max_tokens"] for request in child.requests] == [47, 47]
    result = child.requests[-1]["messages"][-1]
    assert result["content"].endswith("[Tool output truncated]")
    assert len(result["content"]) == 96


@pytest.mark.asyncio
async def test_builtin_task_records_context_stop_without_dispatching_child_model(tmp_path):
    agent = make_agent(tmp_path)
    agent.config.llm.max_context_tokens = 5000
    agent.config.trace_dir = tmp_path / "traces"
    agent.agent_registry.register(AgentType("large_context", "Large instructions", [], "x" * 30000))
    agent.client = RecordingProvider(tool_turn(call("Task", json.dumps({
        "description": "Check the child", "prompt": "Inspect the request.", "agent_type": "large_context",
    }))), _response(content="done"))
    child = RecordingProvider()
    agent.subagent_executor.client = child

    history = await agent.run_loop([{"role": "user", "content": "Delegate a check."}])
    assert not child.requests
    assert "context_window_exceeded" in next(message["content"] for message in history if message["role"] == "tool")
    run, = records(agent.config.trace_dir)
    assert not any(event["event"] == "subagent_model_start" for event in run)
    child_end, = [event for event in run if event["event"] == "subagent_end"]
    assert child_end["status"] == "context_window_exceeded"


@pytest.mark.asyncio
async def test_optional_compaction_does_not_replace_history_with_a_partial_summary(tmp_path):
    agent = make_agent(tmp_path)
    agent.config.llm.max_context_tokens = 5000
    response = _response(content="done")
    response.usage = SimpleNamespace(prompt_tokens=4500, completion_tokens=1, total_tokens=4501)
    agent.client = RecordingProvider(response)
    prior = [
        {"role": "user", "content": "a" * 30000},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "current question"},
    ]
    history = await agent.run_loop(list(prior))

    request, = agent.client.requests
    assert request["messages"] == prior[-1:]
    assert history[:-1] == prior
    assert history[-1]["content"] == "done"
