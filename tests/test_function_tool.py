import json
from types import SimpleNamespace
from typing import Literal

import pytest

from src.minibot.config.schema import Config, LLMConfig, MemoryConfig, SessionConfig, TeamsConfig
from src.minibot.sdk import Minibot
from src.minibot.session.manager import SessionManager
from src.minibot.tools.builtin.ask_user import AskUserQuestionTool, normalize_ask_user_prompt_and_options
from src.minibot.tools.function import FunctionTool, schema_from_callable


def _tool_response(*, name: str, arguments: dict, tool_call_id: str = "call-1"):
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
        usage=None,
    )


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
