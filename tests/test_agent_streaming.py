from types import SimpleNamespace

import asyncio
import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import Config, LLMConfig, TeamsConfig
from src.minibot.events import AsyncQueueEventSink


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._index = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        item = self._chunks[self._index]
        self._index += 1
        return item

    async def close(self):
        self.closed = True


def _chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _response(*, content: str, finish_reason: str = "stop", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _assert_assistant_message(message, content: str) -> None:
    assert message["role"] == "assistant"
    assert message["content"] == content
    assert isinstance(message.get("message_id"), str)


def _make_agent(tmp_path, *, role="solo", teams_enabled=False):
    cfg = Config(
        workdir=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=True,
        ),
        teams=TeamsConfig(enabled=teams_enabled, quiet_teammates=True, debug_teammate_output=False),
    )
    agent = Agent(
        config=cfg,
        role=role,
        team_id="team-1" if role == "teammate" else None,
        member_id="member-1" if role == "teammate" else None,
    )
    agent.silent = True
    agent.status_enabled = False
    return agent


@pytest.mark.asyncio
async def test_run_loop_streaming_happy_path(tmp_path):
    agent = _make_agent(tmp_path)

    class StreamOnlyClient:
        model = "test-model"

        def __init__(self):
            self.stream_calls = 0
            self.async_calls = 0

        async def create_message_stream_async(self, **_kwargs):
            self.stream_calls += 1
            return FakeStream(
                [
                    _chunk(content="hello "),
                    _chunk(content="world"),
                    _chunk(finish_reason="stop"),
                ]
            )

        async def create_message_async(self, **_kwargs):
            self.async_calls += 1
            return _response(content="should-not-be-used")

    client = StreamOnlyClient()
    agent.client = client
    history = [{"role": "user", "content": "say hi"}]

    result = await agent.run_loop(history)

    assert client.stream_calls == 1
    assert client.async_calls == 0
    _assert_assistant_message(result[-1], "hello world")


@pytest.mark.asyncio
async def test_run_loop_extracts_reasoning_and_usage_from_stream_chunks(tmp_path):
    agent = _make_agent(tmp_path)
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=34, total_tokens=46)

    class ReasoningClient:
        model = "test-model"

        async def create_message_stream_async(self, **_kwargs):
            return FakeStream(
                [
                    _chunk(
                        content=[
                            SimpleNamespace(type="reasoning", text="step "),
                            SimpleNamespace(type="text", text="answer"),
                        ]
                    ),
                    _chunk(
                        content=[SimpleNamespace(type="reasoning", text="by step")],
                        finish_reason="stop",
                        usage=usage,
                    ),
                ]
            )

        async def create_message_async(self, **_kwargs):
            raise AssertionError("fallback should not be used")

    agent.client = ReasoningClient()
    history = [{"role": "user", "content": "hello"}]

    result = await agent.run_loop(history)

    _assert_assistant_message(result[-1], "answer")
    assert result[-1]["reasoning"] == "step by step"
    assert result[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}


@pytest.mark.asyncio
async def test_run_loop_extracts_reasoning_from_top_level_output_chunks(tmp_path):
    agent = _make_agent(tmp_path)
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=7, total_tokens=12)

    class OutputChunkClient:
        model = "test-model"

        async def create_message_stream_async(self, **_kwargs):
            return FakeStream(
                [
                    SimpleNamespace(
                        output=[
                            SimpleNamespace(type="reasoning", text="mapped "),
                            SimpleNamespace(type="text", text="answer"),
                        ]
                    ),
                    SimpleNamespace(
                        output=[SimpleNamespace(type="reasoning", output_text="thinking")],
                        finish_reason="stop",
                        usage=usage,
                    ),
                ]
            )

        async def create_message_async(self, **_kwargs):
            raise AssertionError("fallback should not be used")

    agent.client = OutputChunkClient()
    history = [{"role": "user", "content": "hello"}]

    result = await agent.run_loop(history)

    _assert_assistant_message(result[-1], "answer")
    assert result[-1]["reasoning"] == "mapped thinking"
    assert result[-1]["usage"] == {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}


@pytest.mark.asyncio
async def test_run_loop_extracts_reasoning_from_nonstandard_non_stream_response(tmp_path):
    agent = _make_agent(tmp_path)
    agent.set_stream_enabled(False)

    class OutputResponseClient:
        model = "test-model"

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("streaming should be disabled")

        async def create_message_async(self, **_kwargs):
            return SimpleNamespace(
                output=[
                    SimpleNamespace(type="reasoning", text="planned "),
                    SimpleNamespace(type="text", text="reply"),
                ],
                finish_reason="stop",
                usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4, total_tokens=13),
            )

    agent.client = OutputResponseClient()
    history = [{"role": "user", "content": "hello"}]

    result = await agent.run_loop(history)

    _assert_assistant_message(result[-1], "reply")
    assert result[-1]["reasoning"] == "planned "
    assert result[-1]["usage"] == {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}


@pytest.mark.asyncio
async def test_run_loop_streaming_slow_chunks_are_not_cancelled_by_polling(tmp_path):
    agent = _make_agent(tmp_path)

    class SlowChunkStream:
        def __init__(self):
            self._index = 0
            self.cancelled = False
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            if self.cancelled:
                raise StopAsyncIteration
            if self._index == 0:
                self._index += 1
                return _chunk(content="slow ")
            if self._index == 1:
                self._index += 1
                return _chunk(content="reply")
            if self._index == 2:
                self._index += 1
                return _chunk(finish_reason="stop")
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    class SlowStreamClient:
        model = "test-model"

        def __init__(self):
            self.stream = SlowChunkStream()

        async def create_message_stream_async(self, **_kwargs):
            return self.stream

        async def create_message_async(self, **_kwargs):
            raise AssertionError("fallback should not be used")

    client = SlowStreamClient()
    agent.client = client
    history = [{"role": "user", "content": "slow reply"}]
    interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)

    result = await agent.run_loop(history, interrupt_queue=interrupt_queue)

    _assert_assistant_message(result[-1], "slow reply")
    assert client.stream.cancelled is False
    assert client.stream.closed is True


@pytest.mark.asyncio
async def test_stream_thinking_status_starts_before_stream_is_ready(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    agent.silent = False
    agent.status_enabled = True

    status_entered = asyncio.Event()
    stream_requested = asyncio.Event()
    release_stream = asyncio.Event()

    class _StatusCtx:
        def __enter__(self):
            status_entered.set()
            return self

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr("src.minibot.agent.status", lambda *_a, **_k: _StatusCtx())
    monkeypatch.setattr("src.minibot.agent.stream_assistant_start", lambda *_a, **_k: None)
    monkeypatch.setattr("src.minibot.agent.stream_assistant_write", lambda *_a, **_k: None)
    monkeypatch.setattr("src.minibot.agent.stream_assistant_end", lambda *_a, **_k: None)

    class DelayedStreamClient:
        model = "test-model"

        async def create_message_stream_async(self, **_kwargs):
            stream_requested.set()
            await release_stream.wait()
            return FakeStream([_chunk(content="ok"), _chunk(finish_reason="stop")])

        async def create_message_async(self, **_kwargs):
            raise AssertionError("fallback should not be used")

    agent.client = DelayedStreamClient()
    history = [{"role": "user", "content": "hello"}]
    interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)

    run_task = asyncio.create_task(agent.run_loop(history, interrupt_queue=interrupt_queue))
    await stream_requested.wait()

    assert status_entered.is_set()

    release_stream.set()
    result = await run_task

    _assert_assistant_message(result[-1], "ok")


@pytest.mark.asyncio
async def test_run_loop_stream_failure_falls_back_and_marks_degraded(tmp_path):
    agent = _make_agent(tmp_path)

    class FallbackClient:
        model = "test-model"

        def __init__(self):
            self.stream_calls = 0
            self.async_calls = 0

        async def create_message_stream_async(self, **_kwargs):
            self.stream_calls += 1
            raise RuntimeError("stream unsupported")

        async def create_message_async(self, **_kwargs):
            self.async_calls += 1
            return _response(content="fallback")

    client = FallbackClient()
    agent.client = client
    history = [{"role": "user", "content": "hello"}]

    result = await agent.run_loop(history)

    assert client.stream_calls == 1
    assert client.async_calls == 1
    _assert_assistant_message(result[-1], "fallback")
    assert agent.get_stream_state()["degraded"] is True


@pytest.mark.asyncio
async def test_stream_tool_call_arguments_are_merged(tmp_path):
    agent = _make_agent(tmp_path)
    executed: list[tuple[str, dict[str, str]]] = []

    async def fake_execute(name: str, args: dict[str, str]) -> str:
        executed.append((name, args))
        return "ok"

    agent._execute_tool_with_hooks = fake_execute  # type: ignore[assignment]

    class ToolCallClient:
        model = "test-model"

        def __init__(self):
            self.stream_calls = 0

        async def create_message_stream_async(self, **_kwargs):
            self.stream_calls += 1
            if self.stream_calls == 1:
                return FakeStream(
                    [
                        _chunk(content="working "),
                        _chunk(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(name="echo", arguments='{"text":"he'),
                                )
                            ]
                        ),
                        _chunk(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    function=SimpleNamespace(arguments='llo"}'),
                                )
                            ]
                        ),
                        _chunk(finish_reason="tool_calls"),
                    ]
                )
            return FakeStream(
                [
                    _chunk(content="done"),
                    _chunk(finish_reason="stop"),
                ]
            )

        async def create_message_async(self, **_kwargs):
            raise AssertionError("fallback should not be used")

    client = ToolCallClient()
    agent.client = client
    history = [{"role": "user", "content": "run tool"}]

    result = await agent.run_loop(history)

    assert executed == [("echo", {"text": "hello"})]
    assert any(item.get("role") == "tool" and item.get("tool_call_id") == "call-1" for item in result)
    _assert_assistant_message(result[-1], "done")


@pytest.mark.asyncio
async def test_teammate_does_not_use_streaming(tmp_path):
    agent = _make_agent(tmp_path, role="teammate", teams_enabled=True)

    class TeammateClient:
        model = "test-model"

        def __init__(self):
            self.stream_calls = 0
            self.async_calls = 0

        async def create_message_stream_async(self, **_kwargs):
            self.stream_calls += 1
            return FakeStream([])

        async def create_message_async(self, **_kwargs):
            self.async_calls += 1
            return _response(content="ok")

    client = TeammateClient()
    agent.client = client
    history = [{"role": "user", "content": "hello"}]

    result = await agent.run_loop(history)

    _assert_assistant_message(result[-1], "ok")
    assert client.stream_calls == 0
    assert client.async_calls == 1


@pytest.mark.asyncio
async def test_whitespace_before_tool_call_does_not_start_stream_render(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    agent.silent = False
    agent.status_enabled = False

    started = {"count": 0}

    def _start(**_kwargs):
        started["count"] += 1

    monkeypatch.setattr("src.minibot.agent.stream_assistant_start", _start)
    monkeypatch.setattr("src.minibot.agent.stream_assistant_write", lambda *_a, **_k: None)
    monkeypatch.setattr("src.minibot.agent.stream_assistant_end", lambda *_a, **_k: None)

    async def fake_execute(name: str, args: dict[str, str]) -> str:
        return "ok"

    agent._execute_tool_with_hooks = fake_execute  # type: ignore[assignment]

    class ToolCallClient:
        model = "test-model"

        def __init__(self):
            self.stream_calls = 0

        async def create_message_stream_async(self, **_kwargs):
            self.stream_calls += 1
            if self.stream_calls == 1:
                return FakeStream(
                    [
                        _chunk(content="   "),
                        _chunk(
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(name="echo", arguments='{"text":"hello"}'),
                                )
                            ]
                        ),
                        _chunk(finish_reason="tool_calls"),
                    ]
                )
            return FakeStream(
                [
                    _chunk(content="done"),
                    _chunk(finish_reason="stop"),
                ]
            )

        async def create_message_async(self, **_kwargs):
            raise AssertionError("fallback should not be used")

    agent.client = ToolCallClient()
    history = [{"role": "user", "content": "run tool"}]

    result = await agent.run_loop(history)

    _assert_assistant_message(result[-1], "done")
    assert started["count"] == 1


@pytest.mark.asyncio
async def test_todo_write_emits_structured_todo_snapshots(tmp_path):
    agent = _make_agent(tmp_path)
    agent.set_stream_enabled(False)
    queue: asyncio.Queue = asyncio.Queue()
    agent.event_sink = AsyncQueueEventSink(queue)

    class TodoClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("streaming should be disabled")

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _response(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(name="TodoWrite", arguments='{"items":[{"content":"Inspect layout","status":"completed","activeForm":"Inspecting layout"},{"content":"Design dock","status":"in_progress","activeForm":"Designing dock"},{"content":"Verify mobile behavior","status":"pending","activeForm":"Verifying mobile behavior"}]}'),
                        )
                    ],
                )
            if self.calls == 2:
                return _response(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-2",
                            function=SimpleNamespace(name="TodoWrite", arguments='{"items":[{"content":"Inspect layout","status":"completed","activeForm":"Inspecting layout"},{"content":"Design dock","status":"completed","activeForm":"Designing dock"},{"content":"Verify mobile behavior","status":"completed","activeForm":"Verifying mobile behavior"}]}'),
                        )
                    ],
                )
            return _response(content="All done")

    agent.client = TodoClient()
    history = [{"role": "user", "content": "Implement the dock"}]

    result = await agent.run_loop(history)

    _assert_assistant_message(result[-1], "All done")

    emitted = []
    while not queue.empty():
        emitted.append(await queue.get())

    todo_events = [event for event in emitted if event.get("type") == "todo_snapshot"]
    assert len(todo_events) == 2

    first_snapshot = todo_events[0]["todo"]
    assert first_snapshot["title"] == "Current plan"
    assert first_snapshot["completed"] == 1
    assert first_snapshot["total"] == 3
    assert first_snapshot["visible"] is True
    assert [item["status"] for item in first_snapshot["items"]] == ["done", "active", "pending"]
    assert first_snapshot["items"][1]["detail"] == "Designing dock"
    assert first_snapshot["completed_at"] is None

    final_snapshot = todo_events[-1]["todo"]
    assert final_snapshot["completed"] == 3
    assert final_snapshot["total"] == 3
    assert final_snapshot["visible"] is True
    assert [item["status"] for item in final_snapshot["items"]] == ["done", "done", "done"]
    assert final_snapshot["completed_at"]
