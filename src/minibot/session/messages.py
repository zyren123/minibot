"""Helpers for normalizing persisted session messages."""

from __future__ import annotations

from typing import Any


def normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    """Return tool calls in OpenAI chat-completions message shape."""
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            continue
        tool_id = str(raw.get("id") or f"tool-call-{index}")
        function = raw.get("function")
        if isinstance(function, dict):
            normalized.append(
                {
                    "id": tool_id,
                    "type": str(raw.get("type") or "function"),
                    "function": {
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or ""),
                    },
                }
            )
            continue

        normalized.append(
            {
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": str(raw.get("name") or ""),
                    "arguments": str(raw.get("arguments") or ""),
                },
            }
        )
    return normalized


def normalize_session_message(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize persisted message fields needed for runtime replay."""
    normalized = dict(message)
    if "tool_calls" in normalized:
        tool_calls = normalize_tool_calls(normalized.get("tool_calls"))
        if tool_calls:
            normalized["tool_calls"] = tool_calls
        else:
            normalized.pop("tool_calls", None)
    return normalized
