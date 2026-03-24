"""Interactive ask-user tool."""

from __future__ import annotations

import json
import re
from typing import Any

from ..base import BaseTool

_NUMBERED_OPTION_RE = re.compile(r"(?:(?<=^)|(?<=[\s\n]))(\d+)[\.\)、．]\s*")


def _clean_option_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().rstrip(",，;；")


def _normalize_option_list(raw_options: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if not isinstance(raw_options, list):
        return options

    for item in raw_options:
        label = ""
        value = ""
        if isinstance(item, str):
            label = _clean_option_label(item)
            value = label
        elif isinstance(item, dict):
            label = _clean_option_label(str(item.get("label") or item.get("text") or item.get("title") or ""))
            value = _clean_option_label(str(item.get("value") or label))
        if not label or not value:
            continue
        options.append({"label": label, "value": value})

    return options


def _extract_numbered_prompt_options(prompt: str) -> tuple[str, list[dict[str, str]]]:
    text = prompt.strip()
    if not text:
        return "", []

    matches = list(_NUMBERED_OPTION_RE.finditer(text))
    if len(matches) < 2:
        return text, []

    intro = text[: matches[0].start()].rstrip()
    if not intro or not (intro.endswith(":") or intro.endswith("：") or "\n" in intro):
        return text, []

    options: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        label = _clean_option_label(text[start:end])
        if not label or len(label) > 180:
            return text, []
        options.append({"label": label, "value": label})

    prompt_text = intro[:-1].rstrip() if intro.endswith(":") or intro.endswith("：") else intro
    return prompt_text or text, options


def normalize_ask_user_prompt_and_options(prompt: str, raw_options: Any) -> tuple[str, list[dict[str, str]]]:
    normalized_prompt = str(prompt or "").strip()
    options = _normalize_option_list(raw_options)
    if options:
        return normalized_prompt, options

    derived_prompt, derived_options = _extract_numbered_prompt_options(normalized_prompt)
    if derived_options:
        return derived_prompt, derived_options
    return normalized_prompt, []


class AskUserQuestionTool(BaseTool):
    """Request a user answer from an interactive client."""

    name = "askuserquestion"
    description = (
        "Ask the user exactly one focused question, wait for the answer, then continue. "
        "Prefer 3-6 concise `options` whenever the user can plausibly choose from a small set of answers. "
        "Only omit `options` when the answer is genuinely open-ended free text."
    )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "A single focused question shown above the choices. "
                        "Do not combine multiple sub-questions here. "
                        "Put selectable answers in `options` instead of embedding example lists or numbered choices in `prompt`."
                    ),
                },
                "options": {
                    "type": "array",
                    "description": (
                        "Optional answer choices. Prefer 3-6 concise options whenever the user can plausibly choose among likely answers."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": "Short answer shown to the user.",
                            },
                            "value": {
                                "type": "string",
                                "description": "Stable value submitted when the option is selected.",
                            },
                        },
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "allow_free_text": {"type": "boolean", "default": True},
                "required": {"type": "boolean", "default": True},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs) -> str:
        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            return json.dumps({"error": "prompt is required"}, ensure_ascii=False)

        prompt, options = normalize_ask_user_prompt_and_options(prompt, kwargs.get("options") or [])

        payload = {
            "prompt": prompt,
            "options": options,
            "allow_free_text": bool(kwargs.get("allow_free_text", True)),
            "required": bool(kwargs.get("required", True)),
        }
        return json.dumps(payload, ensure_ascii=False)
