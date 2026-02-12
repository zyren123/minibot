from types import SimpleNamespace

import asyncio
import pytest

from src.minibot.agent import Agent, UserInterruptedError
from src.minibot.config.schema import Config, LLMConfig, TeamsConfig


def _make_agent(tmp_path):
    cfg = Config(
        workdir=tmp_path,
        llm=LLMConfig(base_url="http://localhost:8000/v1", api_key="test", model="test-model"),
        teams=TeamsConfig(enabled=False),
    )
    agent = Agent(config=cfg)
    agent.silent = True
    agent.status_enabled = False
    return agent


def _make_response(content: str = "ok"):
    message = SimpleNamespace(content=content, tool_calls=[])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_run_loop_interrupts_on_esc(tmp_path):
    agent = _make_agent(tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingClient:
        model = "test-model"

        async def create_message_async(self, **_kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    agent.client = BlockingClient()
    history = [{"role": "user", "content": "hello"}]
    interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)

    loop_task = asyncio.create_task(agent.run_loop(history, interrupt_queue=interrupt_queue))
    await started.wait()
    interrupt_queue.put_nowait(None)

    with pytest.raises(UserInterruptedError):
        await loop_task

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_run_loop_without_interrupt_queue_unchanged(tmp_path):
    agent = _make_agent(tmp_path)
    response = _make_response("done")

    class OneShotClient:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def create_message_async(self, **_kwargs):
            self.calls += 1
            return response

    client = OneShotClient()
    agent.client = client
    history = [{"role": "user", "content": "hello"}]

    result = await agent.run_loop(history)

    assert result[-1] == {"role": "assistant", "content": "done"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_stale_interrupt_token_is_drained(tmp_path):
    agent = _make_agent(tmp_path)
    response = _make_response("ok")

    class OneShotClient:
        model = "test-model"

        async def create_message_async(self, **_kwargs):
            return response

    agent.client = OneShotClient()
    history = [{"role": "user", "content": "hello"}]
    interrupt_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
    interrupt_queue.put_nowait(None)

    result = await agent.run_loop(history, interrupt_queue=interrupt_queue)

    assert result[-1] == {"role": "assistant", "content": "ok"}
    assert interrupt_queue.empty()
