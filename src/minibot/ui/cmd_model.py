"""/model command handler — interactive model configuring."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Coroutine, Any

try:
    from dotenv import dotenv_values, set_key
except ImportError:
    dotenv_values, set_key = None, None  # type: ignore

from ..utils.output import print_panel, print_system
from .interactive import interactive_choice


async def handle_model_cmd(
    raw_args: str,
    app_home: Path,
    prompt_fn: Callable[[str, str], Coroutine[Any, Any, str]],
) -> None:
    """Dispatch /model subcommands."""
    sub = raw_args.strip().split()[0].lower() if raw_args.strip() else ""

    if sub == "config" or sub == "":
        await _interactive_config(app_home, prompt_fn)
    else:
        _print_usage()


async def _interactive_config(
    app_home: Path,
    prompt_fn: Callable[[str, str], Coroutine[Any, Any, str]]
) -> None:
    """Show interactive arrow-key selector for .env model configurations."""
    if dotenv_values is None or set_key is None:
        print_system("Error: python-dotenv is not installed. Unable to manage .env file.")
        return

    env_path = app_home / ".env"
    
    if not env_path.exists():
        print_system(f"Error: {env_path} does not exist. Please initialize .env first.")
        return

    keys_to_manage = ["OPENAI_BASE_URL", "OPENAI_API_KEY", "MODEL_ID"]

    while True:
        # Load current values
        current_values = dotenv_values(env_path)

        items = [{"key": k, "value": current_values.get(k, "")} for k in keys_to_manage]

        def _render(item: dict[str, str], idx: int, cur: int) -> str:
            arrow = "▸ " if idx == cur else "  "
            val = item["value"]
            if item["key"] == "OPENAI_API_KEY" and val and len(val) > 10:
                val = val[:5] + "..." + val[-3:]  # Mask API key visually
            elif not val:
                val = "<not set>"
            return f"{arrow}{item['key']:<16} = {val}"

        selected = await interactive_choice(items, render_fn=_render)
        
        if selected is None:
            # User pressed q or Esc
            break
            
        key = selected["key"]
        old_val = selected["value"] or ""
        
        rich_label = f"[bold cyan]Enter new value for {key}[/] (current: {old_val}): "
        plain_label = f"Enter new value for {key} (current: {old_val}): "
        
        try:
            new_val = await prompt_fn(rich_label, plain_label)
        except (EOFError, KeyboardInterrupt):
            print_system("\nAborted.")
            continue
            
        new_val = new_val.strip()
        if new_val and new_val != old_val:
            set_key(env_path, key, new_val)
            print_system(f"Updated {key} successfully in {env_path}\n")
        else:
            print_system("No changes made.\n")


def _print_usage() -> None:
    usage = "/model config    Interactive model configuration selector (↑↓ navigate, Enter select)"
    print_panel("Model Commands", usage)
