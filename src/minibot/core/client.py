"""LLM Client wrapper."""

import os
from typing import Any

from openai import AsyncOpenAI, OpenAI


class LLMClient:
    """Wrapper for LLM API client."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("MODEL_ID", "gpt-4.1-mini")

        self._sync_client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )
        self._async_client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )
        self._closed = False

    def _reasoning_kwargs(self, reasoning_effort: str | None) -> dict[str, Any]:
        effort = str(reasoning_effort or "").strip().lower()
        if effort not in {"low", "medium", "high"}:
            return {}
        base_url = str(self.base_url or "").lower()
        if "openrouter.ai" in base_url:
            return {"reasoning": {"effort": effort}}
        return {"reasoning_effort": effort}

    @staticmethod
    def _is_reasoning_effort_unsupported_error(exc: Exception) -> bool:
        text = str(exc).lower()
        param_markers = (
            "reasoning_effort",
            "reasoning effort",
            "reasoning.effort",
            '"reasoning"',
            "'reasoning'",
        )
        unsupported_markers = (
            "unsupported",
            "unknown parameter",
            "unexpected",
            "unrecognized",
            "not permitted",
            "extra inputs",
            "invalid",
        )
        return any(marker in text for marker in param_markers) and any(
            marker in text for marker in unsupported_markers
        )

    def _build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str,
        tools: list[dict] | None,
        max_tokens: int,
        stream: bool = False,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Build request kwargs for chat completions."""
        chat_messages = []
        if system:
            chat_messages.append({"role": "system", "content": system})

        chat_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
        }
        kwargs.update(self._reasoning_kwargs(reasoning_effort))
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    def _create_sync_with_fallback(self, kwargs: dict[str, Any]) -> Any:
        try:
            return self._sync_client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not any(key in kwargs for key in ("reasoning_effort", "reasoning")):
                raise
            if not self._is_reasoning_effort_unsupported_error(exc):
                raise
            fallback_kwargs = {
                key: value
                for key, value in kwargs.items()
                if key not in {"reasoning_effort", "reasoning"}
            }
            return self._sync_client.chat.completions.create(**fallback_kwargs)

    async def _create_async_with_fallback(self, kwargs: dict[str, Any]) -> Any:
        try:
            return await self._async_client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not any(key in kwargs for key in ("reasoning_effort", "reasoning")):
                raise
            if not self._is_reasoning_effort_unsupported_error(exc):
                raise
            fallback_kwargs = {
                key: value
                for key, value in kwargs.items()
                if key not in {"reasoning_effort", "reasoning"}
            }
            return await self._async_client.chat.completions.create(**fallback_kwargs)

    def create_message(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
        reasoning_effort: str | None = None,
    ) -> Any:
        """Create a message with the LLM (deprecated sync compatibility path)."""
        kwargs = self._build_kwargs(
            model=self.model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        return self._create_sync_with_fallback(kwargs)

    async def create_message_async(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
        reasoning_effort: str | None = None,
    ) -> Any:
        """Create a message with the async OpenAI client."""
        kwargs = self._build_kwargs(
            model=self.model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        return await self._create_async_with_fallback(kwargs)

    async def create_message_stream_async(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
        reasoning_effort: str | None = None,
    ) -> Any:
        """Create a streaming message with the async OpenAI client."""
        kwargs = self._build_kwargs(
            model=self.model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            stream=True,
            reasoning_effort=reasoning_effort,
        )
        return await self._create_async_with_fallback(kwargs)

    async def close(self) -> None:
        """Close underlying async client resources."""
        if self._closed:
            return
        if hasattr(self._sync_client, "close"):
            self._sync_client.close()
        await self._async_client.close()
        self._closed = True
