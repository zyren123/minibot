"""CLI ask-user interaction helpers."""

from __future__ import annotations

from typing import Any, Awaitable, Protocol

from ..utils.output import print_system, prompt_input
from ..utils.rich_utils import get_console, rich_enabled
from .interactive import interactive_pick_option_or_custom


class PromptFn(Protocol):
    async def __call__(self, *, rich_label: str, plain_label: str) -> str: ...


async def ask_user_question_cli(
    question: dict[str, Any],
    *,
    prompt_fn: PromptFn | None = None,
) -> dict[str, Any]:
    prompt = str(question.get("prompt") or "").strip()
    options = list(question.get("options") or [])
    allow_free_text = bool(question.get("allow_free_text", True))

    if prompt and not options:
        print_system(prompt)

    if options:
        selection = await interactive_pick_option_or_custom(prompt, options, allow_free_text=allow_free_text)
        if selection is not None:
            if selection.get("kind") == "option":
                answer_text = str(selection.get("label") or "")
                _print_user_answer(answer_text)
                return {"answer_text": answer_text, "selected_option_value": str(selection.get("value") or "")}
            if selection.get("kind") == "custom":
                answer_text = str(selection.get("text") or "").strip()
                _print_user_answer(answer_text)
                return {"answer_text": answer_text, "selected_option_value": None}
            if selection.get("kind") == "custom_prompt":
                text = await _prompt_custom_answer(prompt, prompt_fn=prompt_fn)
                return {"answer_text": text, "selected_option_value": None}

    text = await _prompt_custom_answer(prompt, prompt_fn=prompt_fn)
    return {"answer_text": text, "selected_option_value": None}


async def _prompt_custom_answer(prompt: str, *, prompt_fn: PromptFn | None = None) -> str:
    if prompt_fn is not None:
        return (await prompt_fn(rich_label="[bold bright_green]You:[/] ", plain_label="You: ")).strip()
    return prompt_input("You: ").strip()


def _print_user_answer(answer_text: str) -> None:
    text = answer_text.strip()
    if not text:
        return
    if rich_enabled():
        get_console().print(f"[bold bright_green]You:[/] {text}")
        return
    print(f"You: {text}")
