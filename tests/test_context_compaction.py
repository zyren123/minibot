"""Tests for context compaction logic in the Agent."""

import pytest
import asyncio
from pathlib import Path
from src.minibot.agent import Agent, UserInterruptedError
from src.minibot.config.schema import Config, LLMConfig
from src.minibot.events import AsyncQueueEventSink
from src.minibot.session.manager import SessionManager


class MockMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

class MockChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason

class MockUsage:
    def __init__(self, total_tokens):
        self.prompt_tokens = total_tokens // 2
        self.completion_tokens = total_tokens // 2
        self.total_tokens = total_tokens

class MockResponse:
    def __init__(self, content, total_tokens=None, finish_reason="stop"):
        self.choices = [MockChoice(MockMessage(content), finish_reason=finish_reason)]
        self.usage = MockUsage(total_tokens) if total_tokens is not None else None


class MockLLMClient:
    def __init__(self):
        self.responses = []
        self.stream_responses = []
        self.call_count = 0
        self.stream_call_count = 0

    async def create_message_async(self, **kwargs):
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return MockResponse("default sync response")

    async def create_message_stream_async(self, **kwargs):
        self.stream_call_count += 1
        
        # Async generator for chunks
        async def _gen():
            if self.stream_responses:
                resp = self.stream_responses.pop(0)
                for chunk in resp:
                    yield chunk
            else:
                yield {"choices": [{"delta": {"content": "default"}, "finish_reason": None}]}
                yield {"choices": [{"delta": {"content": " stream response"}, "finish_reason": "stop"}], "usage": MockUsage(100)}
                
        class AsyncIter:
            def __aiter__(self_iter):
                return _gen()
                
        return AsyncIter()

    async def close(self):
        pass


@pytest.fixture
def mock_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key")
    config = Config(
        workdir=tmp_path,
        llm=LLMConfig(max_context_tokens=1000, stream_enabled=False),  # Set low threshold
    )
    agent = Agent(config=config, role="solo")
    agent.client = MockLLMClient()
    agent.silent = True
    queue: asyncio.Queue = asyncio.Queue()
    agent.event_sink = AsyncQueueEventSink(queue)
    agent._test_event_queue = queue
    return agent


@pytest.mark.asyncio
async def test_agent_run_loop_triggers_compaction(mock_agent):
    # Set up the mock client to return a high token usage to trigger compaction
    mock_agent.client.responses = [
        # 1. Normal response but with extremely high token usage (850 > 80% of 1000)
        MockResponse("This message makes the context very long.", total_tokens=850),
        # 2. Compaction summarization response
        MockResponse("This is the compacted summary."),
    ]
    
    messages = [
        {"role": "user", "content": "Hello!"}
    ]
    
    final_messages = await mock_agent.run_loop(messages)
    
    assert mock_agent.client.call_count == 2, "Agent should have called LLM twice (normal + compaction)"
    assert len(final_messages) == 2, "Expected exactly 2 messages (compacted summary + final assistant reply)"
    assert final_messages[0]["is_compaction"] is True
    assert "Context Compacted" in final_messages[0]["content"]
    assert "This is the compacted summary" in final_messages[0]["content"]
    assert final_messages[0]["context_usage"]["total_tokens"] > 0
    assert final_messages[1]["context_usage"] == final_messages[0]["context_usage"]

    emitted = []
    while not mock_agent._test_event_queue.empty():
        emitted.append(await mock_agent._test_event_queue.get())
    compaction_event = next(
        event
        for event in emitted
        if event.get("type") == "system" and event.get("data", {}).get("context_compacted") is True
    )
    assert compaction_event["data"]["context_usage"]["total_tokens"] > 0
    assert compaction_event["data"]["auto_compact_threshold_tokens"] == 800


@pytest.mark.asyncio
async def test_session_manager_skips_before_compaction(tmp_path):
    session_mgr = SessionManager(tmp_path / "sessions")
    s_id = session_mgr.create()
    
    messages_to_write = [
        {"role": "user", "content": "msg 1"},
        {"role": "assistant", "content": "msg 2"},
        {"role": "user", "content": "msg 3"},
        {"role": "assistant", "content": "Compacted form", "is_compaction": True},
        {"role": "user", "content": "msg 4"},
    ]
    
    for msg in messages_to_write:
        session_mgr.append_message(s_id, msg)
        
    loaded = session_mgr.load(s_id)
    assert len(loaded) == 2
    assert loaded[0]["content"] == "Compacted form"
    assert loaded[0].get("is_compaction") is True
    assert loaded[1]["content"] == "msg 4"
