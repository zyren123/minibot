"""LLM Client wrapper."""

import os
from typing import Any

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)


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

        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

    def create_message(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        max_tokens: int = 8000,
    ) -> Any:
        """Create a message with the LLM."""
        # 构建消息格式 (OpenAI chat API 需要)
        chat_messages = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend(messages)

        kwargs = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        return self._client.chat.completions.create(**kwargs)
