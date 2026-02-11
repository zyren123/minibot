"""Helpers for parsing model tool-call arguments."""

from __future__ import annotations

import json
from typing import Any


def _strip_fenced_json(text: str) -> str:
    """Unwrap ```json fenced blocks if present."""
    candidate = text.strip()
    if not (candidate.startswith("```") and candidate.endswith("```")):
        return candidate

    lines = candidate.splitlines()
    if len(lines) < 2:
        return candidate
    if lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return candidate


def parse_tool_arguments(raw: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Parse tool arguments from model output.

    Returns `(parsed_dict, note)`:
    - `parsed_dict` is `None` when unrecoverable.
    - `note` is warning/error detail (None when clean parse).
    """
    if raw is None:
        return {}, None

    text = _strip_fenced_json(raw)
    if not text:
        return {}, None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        try:
            parsed, end = decoder.raw_decode(text)
        except json.JSONDecodeError:
            return None, f"{exc.msg} at line {exc.lineno} column {exc.colno}"

        trailing = text[end:].strip()
        if not isinstance(parsed, dict):
            return None, f"arguments must be JSON object, got {type(parsed).__name__}"
        if trailing:
            return parsed, "Trailing content after JSON object was ignored"
        return parsed, None

    if not isinstance(parsed, dict):
        return None, f"arguments must be JSON object, got {type(parsed).__name__}"
    return parsed, None
