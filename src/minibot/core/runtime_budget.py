"""Shared limits for model and tool dispatch during one execution segment."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from typing import Any

from ..config.schema import RuntimeBudgetConfig


class RuntimeBudgetExceeded(RuntimeError):
    """A run reached one of its configured limits."""

    def __init__(self, limit: str):
        self.limit = limit
        super().__init__(f"budget_exceeded: {limit}")


@dataclass(frozen=True)
class ModelAllowance:
    input_estimate: int
    max_tokens: int


def _estimate_text_tokens(text: str) -> int:
    if text.isascii():
        return (len(text) + 3) // 4
    ascii_chars = sum(char.isascii() for char in text)
    return (ascii_chars + 3) // 4 + len(text) - ascii_chars


def _estimate_payload_tokens(payload: Any) -> int:
    return _estimate_text_tokens(json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")))


def _reported_tokens(usage: Any, field: str) -> int | None:
    reported = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
    return reported if type(reported) is int and reported >= 0 else None


class RuntimeBudget:
    """Owns counters and a deadline on the current event loop."""

    def __init__(self, limits: RuntimeBudgetConfig):
        self.limits = limits
        self.loop = asyncio.get_running_loop()
        self.deadline = self.loop.time() + limits.max_wall_time
        self.llm_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _check_time(self) -> None:
        if self.loop.time() >= self.deadline:
            raise RuntimeBudgetExceeded("max_wall_time")

    @asynccontextmanager
    async def time_limit(self):
        self._check_time()
        timer = asyncio.timeout_at(self.deadline)
        try:
            async with timer:
                yield
        except TimeoutError as exc:
            if not timer.expired():
                raise
            raise RuntimeBudgetExceeded("max_wall_time") from exc

    @contextmanager
    def pause(self):
        started = self.loop.time()
        try:
            yield
        finally:
            self.deadline += self.loop.time() - started

    def start_model(
        self, messages: list[dict], system: str, tools: list[dict] | None, max_tokens: int,
    ) -> ModelAllowance:
        self._check_time()
        if self.llm_calls >= self.limits.max_llm_calls:
            raise RuntimeBudgetExceeded("max_llm_calls")
        remaining_input = self.limits.max_input_tokens - self.input_tokens
        input_estimate = _estimate_payload_tokens((system, messages, tools))
        if remaining_input < input_estimate:
            raise RuntimeBudgetExceeded("max_input_tokens")
        remaining_output = self.limits.max_output_tokens - self.output_tokens
        if remaining_output <= 0:
            raise RuntimeBudgetExceeded("max_output_tokens")
        self.llm_calls += 1
        self.input_tokens += input_estimate
        return ModelAllowance(input_estimate, min(max_tokens, remaining_output))

    def finish_model(
        self,
        allowance: ModelAllowance,
        usage: Any,
        output: tuple[str, str | None, list[Any]],
    ) -> None:
        prompt_tokens = _reported_tokens(usage, "prompt_tokens")
        completion_tokens = _reported_tokens(usage, "completion_tokens")
        if prompt_tokens is not None:
            self.input_tokens += prompt_tokens - allowance.input_estimate
        if completion_tokens is None:
            text, reasoning, tool_calls = output
            # Compatible providers can surface the same text through both extractors.
            distinct_reasoning = reasoning if reasoning and reasoning != text else ""
            completion_tokens = _estimate_text_tokens(text + distinct_reasoning)
            if tool_calls:
                completion_tokens += _estimate_payload_tokens(tool_calls)
        self.output_tokens += completion_tokens
        if self.input_tokens > self.limits.max_input_tokens:
            raise RuntimeBudgetExceeded("max_input_tokens")
        if self.output_tokens > self.limits.max_output_tokens:
            raise RuntimeBudgetExceeded("max_output_tokens")

    def start_tool(self) -> None:
        self._check_time()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise RuntimeBudgetExceeded("max_tool_calls")
        self.tool_calls += 1


current_budget: ContextVar[RuntimeBudget | None] = ContextVar("runtime_budget", default=None)
