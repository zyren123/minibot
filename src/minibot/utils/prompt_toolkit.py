"""Prompt-toolkit based interactive input (optional).

This module is intentionally optional: if prompt_toolkit is not installed or
stdin/stdout is not a TTY, callers should fall back to builtin input().
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str


def prompt_toolkit_enabled() -> bool:
    import os
    import sys

    env = os.environ.get("MINIBOT_PROMPT_TOOLKIT", "1").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False

    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:
        return False

    try:
        import prompt_toolkit  # noqa: F401
    except Exception:
        return False

    return True


class PromptToolkitInput:
    def __init__(self, *, history_path: Path, commands: Iterable[SlashCommand]):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style

        self._commands = tuple(commands)
        self._history_path = history_path
        history_path.parent.mkdir(parents=True, exist_ok=True)

        self._style = Style.from_dict(
            {
                "prompt": "ansibrightgreen bold",
            }
        )

        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_path)),
            completer=_SlashCommandCompleter(self._commands),
            complete_while_typing=True,
            style=self._style,
        )

    async def prompt_async(self, *, label: str = "You: ") -> str:
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.patch_stdout import patch_stdout

        prompt = HTML(f"<prompt>{label}</prompt>")
        with patch_stdout():
            return (await self._session.prompt_async(prompt)).strip()

    async def confirm_async(self, prompt: str, *, default: bool = False) -> bool:
        from prompt_toolkit.patch_stdout import patch_stdout

        suffix = " [Y/n] " if default else " [y/N] "
        while True:
            with patch_stdout():
                text = (await self._session.prompt_async(prompt + suffix)).strip().lower()
            if not text:
                return default
            if text in {"y", "yes"}:
                return True
            if text in {"n", "no"}:
                return False

    def prompt(self, *, label: str = "You: ") -> str:
        """Synchronous wrapper for non-async entrypoints."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.prompt_async(label=label))
        raise RuntimeError("PromptToolkitInput.prompt() cannot be used inside a running event loop; use prompt_async().")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        """Synchronous wrapper for non-async entrypoints."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.confirm_async(prompt, default=default))
        raise RuntimeError(
            "PromptToolkitInput.confirm() cannot be used inside a running event loop; use confirm_async()."
        )


class _SlashCommandCompleter:
    def __init__(self, commands: Iterable[SlashCommand]):
        self._commands = tuple(commands)

    def get_completions(self, document, complete_event):  # noqa: ANN001
        from prompt_toolkit.completion import Completion

        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if any(ch.isspace() for ch in text):
            return

        for cmd in self._iter_matches(text):
            yield Completion(
                cmd.name,
                start_position=-len(text),
                display=cmd.name,
                display_meta=cmd.description,
            )

    async def get_completions_async(self, document, complete_event):  # noqa: ANN001
        # prompt_toolkit expects this method for async completion. We keep the
        # implementation synchronous and expose an async wrapper.
        for completion in self.get_completions(document, complete_event) or ():
            yield completion

    def _iter_matches(self, prefix: str) -> Iterable[SlashCommand]:
        for cmd in self._commands:
            if cmd.name.startswith(prefix):
                yield cmd
