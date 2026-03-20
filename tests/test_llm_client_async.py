from types import SimpleNamespace

import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import Config, LLMConfig, TeamsConfig
from src.minibot.core.client import LLMClient


def _install_fake_openai_clients(monkeypatch):
    records: dict[str, object] = {"close_calls": 0}

    class FakeSyncCompletions:
        def create(self, **kwargs):
            records["sync_create_kwargs"] = kwargs
            return {"kind": "sync"}

    class FakeAsyncCompletions:
        async def create(self, **kwargs):
            records["async_create_kwargs"] = kwargs
            if kwargs.get("stream"):
                return {"kind": "stream"}
            return {"kind": "async"}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            records["sync_init_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeSyncCompletions())

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            records["async_init_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeAsyncCompletions())

        async def close(self):
            records["close_calls"] = int(records["close_calls"]) + 1

    monkeypatch.setattr("src.minibot.core.client.OpenAI", FakeOpenAI)
    monkeypatch.setattr("src.minibot.core.client.AsyncOpenAI", FakeAsyncOpenAI)
    return records


@pytest.mark.asyncio
async def test_create_message_async_uses_async_client(monkeypatch):
    records = _install_fake_openai_clients(monkeypatch)
    client = LLMClient(base_url="http://localhost:8000/v1", api_key="test", model="test-model")

    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]
    result = await client.create_message_async(
        messages=messages,
        system="system prompt",
        tools=tools,
        max_tokens=1234,
    )

    assert result == {"kind": "async"}
    assert records["async_init_kwargs"] == {
        "base_url": "http://localhost:8000/v1",
        "api_key": "test",
    }
    assert records["async_create_kwargs"] == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 1234,
        "tools": tools,
    }


@pytest.mark.asyncio
async def test_create_message_async_maps_reasoning_effort_for_openrouter(monkeypatch):
    records = _install_fake_openai_clients(monkeypatch)
    client = LLMClient(base_url="https://openrouter.ai/api/v1", api_key="test", model="test-model")

    result = await client.create_message_async(
        messages=[{"role": "user", "content": "hello"}],
        system="system prompt",
        reasoning_effort="high",
    )

    assert result == {"kind": "async"}
    assert records["async_create_kwargs"]["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in records["async_create_kwargs"]


@pytest.mark.asyncio
async def test_create_message_stream_async_sets_stream_flag(monkeypatch):
    records = _install_fake_openai_clients(monkeypatch)
    client = LLMClient(base_url="http://localhost:8000/v1", api_key="test", model="test-model")

    messages = [{"role": "user", "content": "hello"}]
    result = await client.create_message_stream_async(
        messages=messages,
        system="system prompt",
        tools=None,
        max_tokens=321,
    )

    assert result == {"kind": "stream"}
    assert records["async_create_kwargs"] == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 321,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


@pytest.mark.asyncio
async def test_create_message_async_retries_without_reasoning_effort_when_unsupported(monkeypatch):
    records: dict[str, object] = {"calls": []}

    class FakeSyncCompletions:
        def create(self, **kwargs):
            return kwargs

    class FakeAsyncCompletions:
        async def create(self, **kwargs):
            calls = records["calls"]
            assert isinstance(calls, list)
            calls.append(kwargs)
            if len(calls) == 1:
                raise ValueError("Unsupported parameter: reasoning_effort")
            return {"kind": "async"}

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeSyncCompletions())

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeAsyncCompletions())

        async def close(self):
            return None

    monkeypatch.setattr("src.minibot.core.client.OpenAI", FakeOpenAI)
    monkeypatch.setattr("src.minibot.core.client.AsyncOpenAI", FakeAsyncOpenAI)

    client = LLMClient(base_url="https://example.invalid/v1", api_key="test", model="test-model")
    result = await client.create_message_async(
        messages=[{"role": "user", "content": "hello"}],
        system="system prompt",
        reasoning_effort="medium",
    )

    assert result == {"kind": "async"}
    calls = records["calls"]
    assert isinstance(calls, list)
    assert calls[0]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in calls[1]


@pytest.mark.asyncio
async def test_llm_client_close_is_idempotent(monkeypatch):
    records = _install_fake_openai_clients(monkeypatch)
    client = LLMClient(base_url="http://localhost:8000/v1", api_key="test", model="test-model")

    await client.close()
    await client.close()

    assert records["close_calls"] == 1


@pytest.mark.asyncio
async def test_agent_end_session_closes_client_once(tmp_path):
    cfg = Config(
        workdir=tmp_path,
        llm=LLMConfig(base_url="http://localhost:8000/v1", api_key="test", model="test-model"),
        teams=TeamsConfig(enabled=False),
    )
    agent = Agent(config=cfg)

    class ClientCloseSpy:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def close(self):
            self.calls += 1

    spy = ClientCloseSpy()
    agent.client = spy

    await agent.end_session()

    assert spy.calls == 1
