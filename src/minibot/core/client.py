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

    @staticmethod
    def _build_kwargs(
        *,
        model: str,
        messages: list[dict],
        system: str,
        tools: list[dict] | None,
        max_tokens: int,
        stream: bool = False,
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
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True
        return kwargs

    def create_message(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
    ) -> Any:
        """Create a message with the LLM (deprecated sync compatibility path)."""
        kwargs = self._build_kwargs(
            model=self.model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
        )
        return self._sync_client.chat.completions.create(**kwargs)

    async def create_message_async(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
    ) -> Any:
        """Create a message with the async OpenAI client."""
        kwargs = self._build_kwargs(
            model=self.model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
        )
        return await self._async_client.chat.completions.create(**kwargs)

    async def create_message_stream_async(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
    ) -> Any:
        """Create a streaming message with the async OpenAI client."""
        kwargs = self._build_kwargs(
            model=self.model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            stream=True,
        )
        return await self._async_client.chat.completions.create(**kwargs)

    async def close(self) -> None:
        """Close underlying async client resources."""
        if self._closed:
            return
        if hasattr(self._sync_client, "close"):
            self._sync_client.close()
        await self._async_client.close()
        self._closed = True
