import asyncio
import json
from types import SimpleNamespace

import pytest

from src.minibot.agent import Agent
from src.minibot.config import RuntimeBudgetConfig
from src.minibot.config.schema import SessionConfig
from src.minibot.core.runtime_budget import RuntimeBudget, RuntimeBudgetExceeded
from src.minibot.events import AsyncQueueEventSink
from src.minibot.sdk import Minibot
from src.minibot.tools.function import FunctionTool
from tests.test_agent_streaming import _chunk, _response
from tests.test_bash_interrupt import config
from tests.test_task_regression import ScriptedProvider, tool_turn
from tests.test_tool_outcomes import call


def make_agent(tmp_path, **limits):
    cfg = config(tmp_path, backend="disabled")
    cfg.runtime_budget = RuntimeBudgetConfig(**limits)
    cfg.trace_dir = tmp_path / "traces"
    agent = Agent(cfg)
    agent.silent = True
    return agent


def user_message():
    return {"role": "user", "content": "Run the task."}


class RepeatingClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def create_message_async(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_model_call_cap_ends_run_and_resets_for_next_run(tmp_path):
    agent = make_agent(tmp_path, max_llm_calls=2)
    event_queue = asyncio.Queue()
    agent.event_sink = AsyncQueueEventSink(event_queue)
    agent.client = RepeatingClient(tool_turn())
    for _ in range(2):
        with pytest.raises(RuntimeBudgetExceeded, match="budget_exceeded: max_llm_calls"):
            await agent.run_loop([user_message()])
    assert len(agent.client.requests) == 4

    events = [event_queue.get_nowait() for _ in range(event_queue.qsize())]
    stop_events = [event["data"] for event in events if event.get("data", {}).get("code") == "budget_exceeded"]
    assert stop_events == [{"code": "budget_exceeded", "limit": "max_llm_calls"}] * 2
    logs = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(logs) == 2
    for path in logs:
        terminal = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        assert (terminal["event"], terminal["status"], terminal["budget_limit"]) == (
            "run_end", "budget_exceeded", "max_llm_calls",
        )


@pytest.mark.asyncio
async def test_stream_fallback_uses_another_model_call(tmp_path):
    agent = make_agent(tmp_path, max_llm_calls=1)
    agent.set_stream_enabled(True)

    class FailedStream(RepeatingClient):
        async def create_message_stream_async(self, **kwargs):
            self.requests.append(kwargs)
            raise ValueError("streaming is unsupported")

    agent.client = FailedStream(_response(content="unexpected fallback"))
    with pytest.raises(RuntimeBudgetExceeded, match="max_llm_calls"):
        await agent.run_loop([user_message()])
    assert len(agent.client.requests) == 1


@pytest.mark.asyncio
async def test_partial_stream_output_is_charged_before_fallback(tmp_path):
    agent = make_agent(tmp_path, max_output_tokens=4)
    agent.set_stream_enabled(True)

    class PartialStream(RepeatingClient):
        async def create_message_stream_async(self, **kwargs):
            self.requests.append(kwargs)

            async def chunks():
                yield _chunk(content="test")
                raise ValueError("stream ended early")

            return chunks()

    agent.client = PartialStream(_response(content="ok"))
    history = await agent.run_loop([user_message()])
    assert history[-1]["content"] == "ok"
    assert [request["max_tokens"] for request in agent.client.requests] == [4, 3]


@pytest.mark.asyncio
async def test_partial_stream_tools_are_charged_before_ids_are_finalized(tmp_path):
    agent = make_agent(tmp_path, max_output_tokens=64)
    agent.set_stream_enabled(True)

    class PartialStream(RepeatingClient):
        async def create_message_stream_async(self, **kwargs):
            self.requests.append(kwargs)

            async def chunks():
                yield _chunk(tool_calls=[{"index": 0, "function": {"arguments": '{"text":'}}])
                raise ValueError("stream ended early")

            return chunks()

    agent.client = PartialStream(_response(content="ok"))
    history = await agent.run_loop([user_message()])
    assert history[-1]["content"] == "ok"
    assert [request["max_tokens"] for request in agent.client.requests] == [64, 44]


@pytest.mark.asyncio
@pytest.mark.parametrize(("stream", "completion_tokens", "fails"), [
    (False, "99", False),
    (False, 99, True),
    (True, "99", True),
    (True, 99, True),
])
async def test_provider_usage_representation_keeps_its_budget_contract(tmp_path, stream, completion_tokens, fails):
    agent = make_agent(tmp_path, max_output_tokens=12)
    agent.set_stream_enabled(stream)
    usage = {"prompt_tokens": 2, "completion_tokens": completion_tokens, "total_tokens": 101}

    class Provider:
        async def create_message_async(self, **kwargs):
            return {"choices": [{"message": {"content": "答复"}, "finish_reason": "stop"}], "usage": usage}

        async def create_message_stream_async(self, **kwargs):
            async def reply():
                yield {"choices": [{"delta": {"content": "答复"}, "finish_reason": "stop"}], "usage": usage}
            return reply()

    agent.client = Provider()
    if fails:
        with pytest.raises(RuntimeBudgetExceeded, match="max_output_tokens"):
            await agent.run_loop([user_message()])
    else:
        result = await agent.run_loop([user_message()])
        assert result[-1]["content"] == "答复"
        assert result[-1]["usage"] == {"prompt_tokens": 2, "completion_tokens": 99, "total_tokens": 101}


@pytest.mark.asyncio
async def test_full_reply_budget_stops_before_display_usage_coercion(tmp_path):
    agent = make_agent(tmp_path, max_output_tokens=12)
    response = _response(content="over budget")
    response.usage = {"completion_tokens": 99, "total_tokens": float("inf")}
    agent.client = RepeatingClient(response)

    with pytest.raises(RuntimeBudgetExceeded, match="max_output_tokens"):
        await agent.run_loop([user_message()])


@pytest.mark.asyncio
async def test_optional_compaction_preserves_finished_reply_at_model_cap(tmp_path):
    agent = make_agent(tmp_path, max_llm_calls=1)
    agent.config.llm.max_context_tokens = 20000
    response = _response(content="finished")
    response.usage = SimpleNamespace(prompt_tokens=16000, completion_tokens=1, total_tokens=16001)
    agent.client = RepeatingClient(response)
    history = await agent.run_loop([user_message()])
    assert history[-1]["content"] == "finished"
    assert len(agent.client.requests) == 1


@pytest.mark.asyncio
async def test_token_usage_clamps_next_request_and_stops_before_third(tmp_path):
    agent = make_agent(tmp_path, max_output_tokens=10)
    first = tool_turn()
    first.usage = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    second = tool_turn()
    second.usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}

    class UsageClient(RepeatingClient):
        async def create_message_async(self, **kwargs):
            self.requests.append(kwargs)
            return (first, second)[len(self.requests) - 1]

    agent.client = UsageClient(first)
    with pytest.raises(RuntimeBudgetExceeded, match="max_output_tokens"):
        await agent.run_loop([user_message()])
    assert [request["max_tokens"] for request in agent.client.requests] == [10, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_first", [False, True])
async def test_concurrent_model_calls_wait_for_shared_output_capacity(tmp_path, cancel_first):
    agent = make_agent(tmp_path, max_output_tokens=10)
    budget = RuntimeBudget(agent.config.runtime_budget)
    entered = asyncio.Event()
    release = asyncio.Event()
    first = _response(content="done")
    first.usage = {"prompt_tokens": 3, "completion_tokens": 7}
    second = _response(content="ok")
    second.usage = {"prompt_tokens": 3, "completion_tokens": 3}

    class BlockingClient(RepeatingClient):
        async def create_message_async(self, **kwargs):
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                entered.set()
                await release.wait()
                return first
            return second

    agent.client = BlockingClient(first)

    async def ask():
        return await agent._budgeted_message(budget, [user_message()], "", None, interrupt_queue=None)

    async with asyncio.timeout(3):
        owner = asyncio.create_task(ask())
        await entered.wait()
        sibling = asyncio.create_task(ask())
        await asyncio.sleep(0)
        assert len(agent.client.requests) == 1
        if cancel_first:
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
        else:
            release.set()
            assert (await owner).content == "done"
        assert (await sibling).content == "ok"
    assert [request["max_tokens"] for request in agent.client.requests] == [10, 10 if cancel_first else 3]


@pytest.mark.asyncio
async def test_input_preflight_rejects_request_without_dispatch(tmp_path):
    agent = make_agent(tmp_path, max_input_tokens=1)
    agent.client = RepeatingClient(_response(content="unexpected dispatch"))
    with pytest.raises(RuntimeBudgetExceeded, match="max_input_tokens"):
        await agent.run_loop([user_message()])
    assert agent.client.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("limits", "limit", "child_tool_count"), [
    ({"max_llm_calls": 1}, "max_llm_calls", 0),
    ({"max_tool_calls": 2}, "max_tool_calls", 1),
])
async def test_task_child_shares_parent_budget(tmp_path, limits, limit, child_tool_count):
    agent = make_agent(tmp_path, **limits)
    task_args = {"description": "Read a fixture", "prompt": "Read the fixture.", "agent_type": "code"}
    agent.client = RepeatingClient(tool_turn(call("Task", json.dumps(task_args))))
    effects = []

    def record() -> str:
        effects.append("record")
        return "recorded"

    agent.tool_registry.register(FunctionTool(record))
    child = RepeatingClient(tool_turn(call("record"), call("record", id="child-2")))
    agent.subagent_executor.client = child
    history = [user_message()]
    with pytest.raises(RuntimeBudgetExceeded, match=limit):
        await agent.run_loop(history, interrupt_queue=asyncio.Queue())
    assert len(agent.client.requests) == 1
    assert len(child.requests) == child_tool_count
    assert effects == ["record"] * child_tool_count
    assert history[-1]["tool_name"] == "Task"
    assert history[-1]["tool_status"] == "refused"
    assert history[-1]["content"] == f"budget_exceeded: {limit}"


@pytest.mark.asyncio
async def test_sdk_retains_prior_tool_results_and_clears_stream_state(tmp_path):
    cfg = config(tmp_path, backend="disabled")
    cfg.runtime_budget = RuntimeBudgetConfig(max_tool_calls=1)
    cfg.session = SessionConfig(sessions_dir=str(tmp_path / "sessions"))
    effects = []

    def record() -> str:
        effects.append("record")
        return "recorded Ω"

    bot = Minibot(config=cfg, tools=[record])
    bot.agent.client = ScriptedProvider(tool_turn(call("record"), call("record", id="call-2")))
    events = [event async for event in bot.stream("Record a synthetic result.")]
    assert effects == ["record"]
    assert any(event.get("data", {}).get("limit") == "max_tool_calls" for event in events)
    assert bot._session_manager.load_runtime_state(bot.session_id) is None
    history = bot._session_manager.load(bot.session_id)
    assistant = next(message for message in history if message.get("tool_calls"))
    results = [message for message in history if message["role"] == "tool"]
    assert [tool["id"] for tool in assistant["tool_calls"]] == ["call-1", "call-2"]
    assert [message["tool_call_id"] for message in results] == ["call-1", "call-2"]
    assert [message["content"] for message in results] == ["recorded Ω", "budget_exceeded: max_tool_calls"]
    assert "budget_exceeded: max_tool_calls" in history[-1]["content"]


@pytest.mark.asyncio
async def test_wall_budget_cancels_active_tool_and_runs_finalizer(tmp_path):
    agent = make_agent(tmp_path, max_wall_time=0.1)
    lifecycle = []

    async def active() -> str:
        lifecycle.append("started")
        try:
            await asyncio.Future()
        finally:
            lifecycle.append("finalized")

    agent.tool_registry.register(FunctionTool(active))
    agent.client = RepeatingClient(tool_turn(call("active"), call("active", id="call-2")))
    history = [user_message()]
    with pytest.raises(RuntimeBudgetExceeded, match="max_wall_time"):
        await asyncio.wait_for(agent.run_loop(history, interrupt_queue=asyncio.Queue()), 2)
    assert lifecycle == ["started", "finalized"]
    assert [message["tool_status"] for message in history if message["role"] == "tool"] == ["timeout", "refused"]


@pytest.mark.asyncio
async def test_wall_budget_closes_stream_and_reaps_pending_chunk(tmp_path):
    agent = make_agent(tmp_path, max_wall_time=0.1)
    agent.set_stream_enabled(True)
    lifecycle = []

    class StalledStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                await asyncio.Future()
            finally:
                lifecycle.append("chunk finalized")

        async def close(self):
            lifecycle.append("stream closed")

    class StreamClient(RepeatingClient):
        async def create_message_stream_async(self, **kwargs):
            self.requests.append(kwargs)
            return StalledStream()

    agent.client = StreamClient(_response(content="unexpected fallback"))
    with pytest.raises(RuntimeBudgetExceeded, match="max_wall_time"):
        await asyncio.wait_for(agent.run_loop([user_message()], interrupt_queue=asyncio.Queue()), 2)
    assert lifecycle == ["chunk finalized", "stream closed"]
    assert len(agent.client.requests) == 1


@pytest.mark.asyncio
async def test_live_answer_wait_does_not_consume_active_wall_budget(tmp_path):
    agent = make_agent(tmp_path, max_wall_time=0.1)

    async def answer(_question):
        await asyncio.sleep(0.15)
        return {"answer_text": "acknowledged"}

    agent.ask_user_handler = answer
    agent.client = ScriptedProvider(
        tool_turn(call("askuserquestion", '{"prompt":"Continue?"}')),
        _response(content="finished"),
    )
    history = await asyncio.wait_for(agent.run_loop([user_message()]), 2)
    assert history[-1]["content"] == "finished"
    assert any(message.get("answer_to_question_id") == "call-1" for message in history)
