"""Prepare, dispatch, and account for one model request."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, TypeVar

from ..config.schema import LLMConfig
from .model_context import prepare_model_request
from .model_reply import ModelCharge
from .runtime_budget import ModelAllowance, RuntimeBudget

_Response = TypeVar("_Response")


@dataclass(frozen=True)
class ModelAttempt:
    _budget: RuntimeBudget
    _allowance: ModelAllowance
    _messages: list[dict]
    _system: str
    _tools: list[dict] | None

    async def dispatch(
        self,
        send: Callable[..., Awaitable[_Response]],
        **options: Any,
    ) -> _Response:
        async with self._budget.time_limit():
            return await send(
                messages=self._messages,
                system=self._system,
                tools=self._tools,
                max_tokens=self._allowance.max_tokens,
                **options,
            )

    def settle(self, charge: ModelCharge) -> None:
        self._budget.finish_model(self._allowance, charge.reported_usage, charge.output)


@asynccontextmanager
async def model_attempt(
    budget: RuntimeBudget,
    llm: LLMConfig,
    messages: list[dict],
    system: str,
    tools: list[dict] | None,
    *,
    max_tokens: int | None = None,
    trim_history: bool = True,
    reserve_output: bool = False,
) -> AsyncIterator[ModelAttempt]:
    if reserve_output:
        await budget.wait_for_output()
    request = prepare_model_request(
        messages,
        system,
        tools,
        budget.output_headroom(llm.max_tokens if max_tokens is None else max_tokens),
        window_tokens=int(llm.max_context_tokens),
        tool_output_chars=llm.max_tool_output_chars,
        trim_history=trim_history,
    )
    allowance = budget.start_model(input_estimate=request.input_estimate, max_tokens=request.max_tokens)
    with budget.reserve_output(allowance) if reserve_output else nullcontext():
        yield ModelAttempt(budget, allowance, request.messages, system, tools)
