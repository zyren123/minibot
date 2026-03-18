import json
from types import SimpleNamespace
from typing import Literal

import pytest

from src.minibot.config.schema import Config, LLMConfig, MemoryConfig, SessionConfig, TeamsConfig
from src.minibot.sdk import Minibot
from src.minibot.session.manager import SessionManager
from src.minibot.tools.function import FunctionTool, schema_from_callable


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

