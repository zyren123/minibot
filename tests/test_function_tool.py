import asyncio
import json
from types import SimpleNamespace
from typing import Literal

import pytest

from src.minibot.config.schema import Config, LLMConfig, MemoryConfig, SessionConfig, TeamsConfig
from src.minibot.sdk import Minibot
from src.minibot.session.manager import SessionManager
from src.minibot.tools.builtin.ask_user import AskUserQuestionTool, normalize_ask_user_prompt_and_options
from src.minibot.tools.function import FunctionTool, schema_from_callable


class _FakeStream:
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
        if isinstance(item, Exception):
            raise item
        if item == "__wait__":
            await asyncio.sleep(60)
        return item

    async def close(self):
        self.closed = True


def _tool_response(
    *,
    name: str,
    arguments: dict,
    tool_call_id: str = "call-1",
    usage=None,
):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id=tool_call_id,
                            function=SimpleNamespace(
                                name=name,
                                arguments=json.dumps(arguments, ensure_ascii=False),
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=usage,
    )


def _stream_chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_schema_from_callable_primitives_and_literal():
    def echo(text: str, count: int = 1, unit: Literal["c", "f"] = "c") -> str:
        return f"{text} {count} {unit}"

    schema = schema_from_callable(echo)
    assert schema["type"] == "object"
    assert schema["properties"]["text"]["type"] == "string"
    assert "text" in schema["required"]
    assert schema["properties"]["count"]["type"] == "integer"
    assert schema["properties"]["count"]["default"] == 1
    assert schema["properties"]["unit"]["enum"] == ["c", "f"]


@pytest.mark.asyncio
async def test_function_tool_executes_sync_and_async():
    def sync_tool(x: int) -> dict:
        return {"x": x}

    async def async_tool(name: str) -> str:
        return f"hi {name}"

    t1 = FunctionTool(sync_tool)
    out1 = await t1.execute(x=3)
    assert json.loads(out1) == {"x": 3}

    t2 = FunctionTool(async_tool)
    out2 = await t2.execute(name="bob")
    assert out2 == "hi bob"


@pytest.mark.asyncio
async def test_ask_user_question_tool_normalizes_string_options():
    tool = AskUserQuestionTool()

    payload = json.loads(
        await tool.execute(
            prompt="Pick a lane",
            options=["Frontend", {"label": "Backend"}],
            allow_free_text=False,
        )
    )

    assert payload["prompt"] == "Pick a lane"
    assert payload["options"] == [
        {"label": "Frontend", "value": "Frontend"},
        {"label": "Backend", "value": "Backend"},
    ]
    assert payload["allow_free_text"] is False


def test_ask_user_question_tool_schema_uses_structured_option_objects():
    schema = AskUserQuestionTool().input_schema

    option_items = schema["properties"]["options"]["items"]

    assert option_items["type"] == "object"
    assert option_items["required"] == ["label", "value"]
    assert option_items["additionalProperties"] is False


def test_normalize_ask_user_prompt_and_options_extracts_numbered_prompt_choices():
    prompt, options = normalize_ask_user_prompt_and_options(
        "Pick one topic: 1. Frontend architecture 2. Backend performance 3. Git workflow",
        [],
    )

    assert prompt == "Pick one topic"
    assert options == [
        {"label": "Frontend architecture", "value": "Frontend architecture"},
        {"label": "Backend performance", "value": "Backend performance"},
        {"label": "Git workflow", "value": "Git workflow"},
    ]


@pytest.mark.asyncio
async def test_minibot_chat_persists_session_and_usage(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hello", tool_calls=[]),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )

    class FakeClient:
        model = "test-model"

        async def create_message_async(self, **_kwargs):
            return response

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()
    result = await bot.chat("hi")

    assert result.assistant_text == "hello"
    assert result.usage and result.usage.get("total_tokens") == 3

    mgr = SessionManager(sessions_dir)
    loaded = mgr.load("sess-1")
    assert loaded[0]["role"] == "user"
    assert loaded[0]["content"] == "hi"
    assert loaded[-1]["role"] == "assistant"
    assert loaded[-1]["content"] == "hello"


@pytest.mark.asyncio
async def test_minibot_registers_callable_tool(tmp_path):
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=False, sessions_dir=str(tmp_path / "sessions")),
    )

    def echo(text: str) -> str:
        return text

    bot = Minibot(config=cfg, tools=[echo])
    out = await bot.agent.tool_registry.execute("echo", {"text": "ok"})
    assert out == "ok"


@pytest.mark.asyncio
async def test_minibot_stream_pauses_for_ask_user_question_and_resumes_with_answer(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-ask-1")

    class FakeClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which task should I handle first?",
                        "options": [
                            {"label": "Shipping", "value": "shipping"},
                            {"label": "Billing", "value": "billing"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-1",
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Proceeding with shipping first.", tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    seen_question_id: str | None = None
    events = []
    async for event in bot.stream("Help me prioritize"):
        events.append(event)
        if event.get("type") == "ask_user_question":
            seen_question_id = event.get("question_id")
            await bot.submit_user_answer(
                question_id=str(seen_question_id),
                answer_text="Shipping",
                selected_option_value="shipping",
            )

    question_event = next(event for event in events if event.get("type") == "ask_user_question")
    answer_event = next(event for event in events if event.get("type") == "ask_user_answer_received")
    final_event = next(event for event in reversed(events) if event.get("type") == "assistant_end")

    assert question_event["prompt"] == "Which task should I handle first?"
    assert question_event["options"][0]["value"] == "shipping"
    assert seen_question_id == question_event["question_id"]
    assert answer_event["question_id"] == seen_question_id
    assert answer_event["answer_text"] == "Shipping"
    assert answer_event["selected_option_value"] == "shipping"
    assert final_event["content"] == "Proceeding with shipping first."

    loaded = SessionManager(sessions_dir).load("sess-ask-1")
    assistant_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("interaction_type") == "askuserquestion"
    )
    user_answer = next(
        message
        for message in loaded
        if message.get("role") == "user" and message.get("answer_to_question_id") == seen_question_id
    )
    assert assistant_question["content"] == "Which task should I handle first?"
    assert assistant_question["question_id"] == seen_question_id
    assert user_answer["content"] == "Shipping"
    assert user_answer["selected_option_value"] == "shipping"


@pytest.mark.asyncio
async def test_minibot_stream_can_stop_at_ask_user_question_for_resume_flow(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-pause-on-question")

    class FakeClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which task should I handle first?",
                        "options": [
                            {"label": "Shipping", "value": "shipping"},
                            {"label": "Billing", "value": "billing"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-live-pause",
                )
            raise AssertionError("stream should stop once the question has been emitted")

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    events = [
        event
        async for event in bot.stream(
            "Help me prioritize",
            stop_on_ask_user_question=True,
        )
    ]

    question_event = next(event for event in events if event.get("type") == "ask_user_question")
    assistant_end_event = next(event for event in events if event.get("type") == "assistant_end")
    assert events[-1]["type"] == "ask_user_question"
    assert question_event["question_id"] == "ask-call-live-pause"
    assert assistant_end_event["tool_calls"] == []

    mgr = SessionManager(sessions_dir)
    runtime = mgr.load_runtime_state("sess-pause-on-question")
    assert runtime == {
        "version": 1,
        "state": "awaiting_user_answer",
        "assistant_message_id": question_event["message_id"],
        "pending_question": {
            "question_id": "ask-call-live-pause",
            "message_id": question_event["message_id"],
            "prompt": "Which task should I handle first?",
            "options": [
                {"label": "Shipping", "value": "shipping"},
                {"label": "Billing", "value": "billing"},
            ],
            "allow_free_text": True,
            "required": True,
        },
    }

    loaded = mgr.load("sess-pause-on-question")
    assistant_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("question_id") == "ask-call-live-pause"
    )
    assert assistant_question["question_pending"] is True
    assert assistant_question.get("completion_state") == "complete"


@pytest.mark.asyncio
async def test_minibot_stream_pause_question_preserves_usage_metadata(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-question-usage")

    class FakeClient:
        model = "test-model"

        async def create_message_async(self, **_kwargs):
            return _tool_response(
                name="askuserquestion",
                arguments={
                    "prompt": "Which task should I handle first?",
                    "options": [
                        {"label": "Shipping", "value": "shipping"},
                        {"label": "Billing", "value": "billing"},
                    ],
                    "allow_free_text": True,
                },
                tool_call_id="ask-call-usage",
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    events = [
        event
        async for event in bot.stream(
            "Help me prioritize",
            stop_on_ask_user_question=True,
        )
    ]

    question_event = next(event for event in events if event.get("type") == "ask_user_question")
    assert question_event["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "total_tokens": 10,
    }

    loaded = SessionManager(sessions_dir).load("sess-question-usage")
    assistant_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("question_id") == "ask-call-usage"
    )
    assert assistant_question["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "total_tokens": 10,
    }


@pytest.mark.asyncio
async def test_minibot_stream_pause_question_preserves_compacted_context_usage(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            max_context_tokens=10,
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-question-context")

    class FakeClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which task should I handle first?",
                        "options": [
                            {"label": "Shipping", "value": "shipping"},
                            {"label": "Billing", "value": "billing"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-context",
                    usage=SimpleNamespace(prompt_tokens=6, completion_tokens=6, total_tokens=12),
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Compacted summary", tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    events = [
        event
        async for event in bot.stream(
            "Help me prioritize",
            stop_on_ask_user_question=True,
        )
    ]

    question_event = next(event for event in events if event.get("type") == "ask_user_question")
    assert question_event["context_compacted"] is True
    assert question_event["context_usage"]["total_tokens"] > 0

    loaded = SessionManager(sessions_dir).load("sess-question-context")
    assistant_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("question_id") == "ask-call-context"
    )
    assert assistant_question["context_usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_minibot_stream_persists_ask_user_history_when_interrupted_during_followup_question(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-ask-interrupted")

    class FakeClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which task should I handle first?",
                        "options": [
                            {"label": "Shipping", "value": "shipping"},
                            {"label": "Billing", "value": "billing"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-1",
                )
            if self.calls == 2:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which region should I focus on next?",
                        "options": [
                            {"label": "APAC", "value": "apac"},
                            {"label": "EMEA", "value": "emea"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-2",
                )
            raise AssertionError("stream should be interrupted before a third model call")

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    second_question_id: str | None = None
    stream = bot.stream("Help me prioritize")
    async for event in stream:
        if event.get("type") != "ask_user_question":
            continue
        question_id = str(event.get("question_id") or "")
        if question_id == "ask-call-1":
            await bot.submit_user_answer(
                question_id=question_id,
                answer_text="Shipping",
                selected_option_value="shipping",
            )
            continue
        second_question_id = question_id
        bot.cancel()
        break
    await stream.aclose()

    for _ in range(20):
        await asyncio.sleep(0)
        loaded = SessionManager(sessions_dir).load("sess-ask-interrupted")
        if any(message.get("question_id") == second_question_id for message in loaded):
            break
    else:
        loaded = SessionManager(sessions_dir).load("sess-ask-interrupted")

    assert second_question_id == "ask-call-2"
    assert loaded[0]["role"] == "user"
    assert loaded[0]["content"] == "Help me prioritize"

    first_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("question_id") == "ask-call-1"
    )
    first_answer = next(
        message
        for message in loaded
        if message.get("role") == "user" and message.get("answer_to_question_id") == "ask-call-1"
    )
    second_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("question_id") == "ask-call-2"
    )

    assert first_question["content"] == "Which task should I handle first?"
    assert first_question["question_pending"] is False
    assert first_answer["content"] == "Shipping"
    assert first_answer["selected_option_value"] == "shipping"
    assert second_question["content"] == "Which region should I focus on next?"
    assert second_question["question_pending"] is True


@pytest.mark.asyncio
async def test_minibot_stream_partial_assistant_output_persists_as_interrupted(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=True,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-partial-stream")

    class FakeClient:
        model = "test-model"

        async def create_message_stream_async(self, **_kwargs):
            return _FakeStream(
                [
                    _stream_chunk(content="Partial "),
                    _stream_chunk(content="answer"),
                    "__wait__",
                ]
            )

        async def create_message_async(self, **_kwargs):
            raise AssertionError("non-streaming path should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    seen_message_id: str | None = None
    stream = bot.stream("Say something")
    async for event in stream:
        if event.get("type") == "assistant_start":
            seen_message_id = str(event.get("message_id") or "")
            continue
        if event.get("type") == "assistant_delta" and event.get("delta_text") == "answer":
            bot.cancel()
            break
    await stream.aclose()

    for _ in range(20):
        await asyncio.sleep(0)
        loaded = SessionManager(sessions_dir).load("sess-partial-stream")
        assistant = next(
            (
                message
                for message in loaded
                if message.get("role") == "assistant" and message.get("message_id") == seen_message_id
            ),
            None,
        )
        if assistant is not None and assistant.get("completion_state") == "interrupted":
            break
    else:
        loaded = SessionManager(sessions_dir).load("sess-partial-stream")
        assistant = next(
            message
            for message in loaded
            if message.get("role") == "assistant" and message.get("message_id") == seen_message_id
        )

    assert seen_message_id
    assert assistant["content"] == "Partial answer"
    assert assistant["completion_state"] == "interrupted"
    assert [message["content"] for message in loaded if message.get("role") == "assistant"] == ["Partial answer"]


@pytest.mark.asyncio
async def test_minibot_stream_persists_runtime_sidecar_until_answer(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )
    bot = Minibot(config=cfg, session_id="sess-await-answer")

    class FakeClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which task should I handle first?",
                        "options": [
                            {"label": "Shipping", "value": "shipping"},
                            {"label": "Billing", "value": "billing"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-sidecar",
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Proceeding with shipping first.", tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()
    stream = bot.stream("Help me prioritize")
    question_event: dict | None = None

    async for event in stream:
        if event.get("type") == "ask_user_question":
            question_event = dict(event)
            break

    assert question_event is not None

    mgr = SessionManager(sessions_dir)
    for _ in range(20):
        await asyncio.sleep(0)
        runtime = mgr.load_runtime_state("sess-await-answer")
        loaded = mgr.load("sess-await-answer")
        assistant = next(
            (
                message
                for message in loaded
                if message.get("role") == "assistant"
                and message.get("question_id") == question_event["question_id"]
            ),
            None,
        )
        if runtime is not None and assistant is not None:
            break
    else:
        runtime = mgr.load_runtime_state("sess-await-answer")
        loaded = mgr.load("sess-await-answer")
        assistant = next(
            message
            for message in loaded
            if message.get("role") == "assistant" and message.get("question_id") == question_event["question_id"]
        )

    assert assistant["content"] == "Which task should I handle first?"
    assert assistant["question_pending"] is True
    assert runtime == {
        "version": 1,
        "state": "awaiting_user_answer",
        "assistant_message_id": question_event["message_id"],
        "pending_question": {
            "question_id": question_event["question_id"],
            "message_id": question_event["message_id"],
            "prompt": "Which task should I handle first?",
            "options": [
                {"label": "Shipping", "value": "shipping"},
                {"label": "Billing", "value": "billing"},
            ],
            "allow_free_text": True,
            "required": True,
        },
    }

    await stream.aclose()


@pytest.mark.asyncio
async def test_minibot_stream_reload_normalizes_persisted_tool_calls_for_next_turn(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )

    def lookup_character(query: str) -> str:
        return f"found {query}"

    bot = Minibot(config=cfg, session_id="sess-tool-history", tools=[lookup_character])

    class ToolCallingClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="lookup_character",
                    arguments={"query": "丁荷月"},
                    tool_call_id="call-tool-1",
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="查到了。", tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )

        async def create_message_stream_async(self, **_kwargs):
            raise RuntimeError("stream unavailable")

        async def close(self):
            return None

    bot.agent.client = ToolCallingClient()
    events = [event async for event in bot.stream("查一下丁荷月")]
    assert any(event.get("type") == "tool_result" for event in events)

    reloaded = Minibot(config=cfg, session_id="sess-tool-history", tools=[lookup_character])

    class RecordingClient:
        model = "test-model"

        def __init__(self):
            self.seen_messages = None

        async def create_message_async(self, **kwargs):
            self.seen_messages = kwargs["messages"]
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="继续吧。", tool_calls=[]),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
            )

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    recording = RecordingClient()
    reloaded.agent.client = recording

    await reloaded.chat("继续")

    assistant_with_tool = next(
        message
        for message in recording.seen_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert assistant_with_tool["tool_calls"] == [
        {
            "id": "call-tool-1",
            "type": "function",
            "function": {
                "name": "lookup_character",
                "arguments": json.dumps({"query": "丁荷月"}, ensure_ascii=False),
            },
        }
    ]


@pytest.mark.asyncio
async def test_minibot_resume_pending_question_stream_stops_at_next_question(tmp_path):
    sessions_dir = tmp_path / "sessions"
    cfg = Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=True, sessions_dir=str(sessions_dir)),
    )

    session_mgr = SessionManager(sessions_dir)
    session_mgr.overwrite(
        "sess-resume-next-question",
        [
            {"role": "user", "content": "Help me prioritize", "message_id": "msg-user-1"},
            {
                "role": "assistant",
                "content": "Which task should I handle first?",
                "message_id": "msg-assistant-1",
                "parent_user_message_id": "msg-user-1",
                "interaction_type": "askuserquestion",
                "question_id": "ask-call-1",
                "question_prompt": "Which task should I handle first?",
                "question_options": [
                    {"label": "Shipping", "value": "shipping"},
                    {"label": "Billing", "value": "billing"},
                ],
                "question_allow_free_text": True,
                "question_required": True,
                "question_pending": False,
            },
            {
                "role": "user",
                "content": "Shipping",
                "message_id": "msg-user-2",
                "answer_to_question_id": "ask-call-1",
                "selected_option_value": "shipping",
            },
        ],
    )
    bot = Minibot(config=cfg, session_id="sess-resume-next-question")

    class FakeClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _tool_response(
                    name="askuserquestion",
                    arguments={
                        "prompt": "Which region should I focus on next?",
                        "options": [
                            {"label": "APAC", "value": "apac"},
                            {"label": "EMEA", "value": "emea"},
                        ],
                        "allow_free_text": True,
                    },
                    tool_call_id="ask-call-2",
                )
            raise AssertionError("resume stream should stop once the next question is emitted")

        async def create_message_stream_async(self, **_kwargs):
            raise AssertionError("stream should not be used")

        async def close(self):
            return None

    bot.agent.client = FakeClient()

    async def _collect_events():
        return [
            event
            async for event in bot.resume_pending_question_stream(
                question_id="ask-call-1",
                answer_text="Shipping",
                selected_option_value="shipping",
            )
        ]

    events = await asyncio.wait_for(_collect_events(), timeout=0.5)

    question_event = next(event for event in events if event.get("type") == "ask_user_question")
    assert question_event["question_id"] == "ask-call-2"
    assert events[-1]["type"] == "ask_user_question"

    runtime = session_mgr.load_runtime_state("sess-resume-next-question")
    assert runtime == {
        "version": 1,
        "state": "awaiting_user_answer",
        "assistant_message_id": question_event["message_id"],
        "pending_question": {
            "question_id": "ask-call-2",
            "message_id": question_event["message_id"],
            "prompt": "Which region should I focus on next?",
            "options": [
                {"label": "APAC", "value": "apac"},
                {"label": "EMEA", "value": "emea"},
            ],
            "allow_free_text": True,
            "required": True,
        },
    }

    loaded = session_mgr.load("sess-resume-next-question")
    second_question = next(
        message
        for message in loaded
        if message.get("role") == "assistant" and message.get("question_id") == "ask-call-2"
    )
    assert second_question["question_pending"] is True
    assert second_question["content"] == "Which region should I focus on next?"
