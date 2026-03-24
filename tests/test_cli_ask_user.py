"""Tests for CLI ask-user interaction helpers."""

from unittest.mock import AsyncMock, patch

import pytest

from src.minibot.ui.ask_user import ask_user_question_cli


@pytest.mark.asyncio
async def test_cli_ask_user_returns_selected_option():
    question = {
        "question_id": "ask-1",
        "prompt": "Which queue?",
        "options": [
            {"label": "Shipping", "value": "shipping"},
            {"label": "Billing", "value": "billing"},
        ],
        "allow_free_text": True,
        "required": True,
    }

    with patch("src.minibot.ui.ask_user.interactive_pick_option_or_custom", new_callable=AsyncMock) as picker:
        with patch("src.minibot.ui.ask_user._print_user_answer") as print_user:
            with patch("src.minibot.ui.ask_user.print_system") as print_system:
                picker.return_value = {"kind": "option", "label": "Shipping", "value": "shipping"}
                result = await ask_user_question_cli(question)

    assert result == {"answer_text": "Shipping", "selected_option_value": "shipping"}
    print_user.assert_called_once_with("Shipping")
    print_system.assert_not_called()


@pytest.mark.asyncio
async def test_cli_ask_user_without_options_prints_prompt_once_then_uses_you_label():
    question = {
        "question_id": "ask-1",
        "prompt": "Which queue?",
        "options": [],
        "allow_free_text": True,
        "required": True,
    }

    async def prompt_fn(*, rich_label: str, plain_label: str) -> str:
        assert rich_label == "[bold bright_green]You:[/] "
        assert plain_label == "You: "
        return "Warehouse triage"

    with patch("src.minibot.ui.ask_user.print_system") as print_system:
        result = await ask_user_question_cli(question, prompt_fn=prompt_fn)

    assert result == {"answer_text": "Warehouse triage", "selected_option_value": None}
    print_system.assert_called_once_with("Which queue?")


@pytest.mark.asyncio
async def test_cli_ask_user_custom_prompt_path_supports_keyword_only_prompt_fn():
    question = {
        "question_id": "ask-1",
        "prompt": "Which queue?",
        "options": [
            {"label": "Shipping", "value": "shipping"},
        ],
        "allow_free_text": True,
        "required": True,
    }

    picker = AsyncMock(return_value={"kind": "custom_prompt"})

    async def prompt_fn(*, rich_label: str, plain_label: str) -> str:
        assert rich_label
        assert plain_label == "You: "
        return "Warehouse triage"

    with patch("src.minibot.ui.ask_user.interactive_pick_option_or_custom", picker):
        with patch("src.minibot.ui.ask_user.print_system") as print_system:
            with patch("src.minibot.ui.ask_user._print_user_answer") as print_user:
                result = await ask_user_question_cli(question, prompt_fn=prompt_fn)

    assert result == {"answer_text": "Warehouse triage", "selected_option_value": None}
    print_system.assert_not_called()
    print_user.assert_not_called()


@pytest.mark.asyncio
async def test_cli_ask_user_returns_custom_text():
    question = {
        "question_id": "ask-1",
        "prompt": "Which queue?",
        "options": [
            {"label": "Shipping", "value": "shipping"},
        ],
        "allow_free_text": True,
        "required": True,
    }

    with patch("src.minibot.ui.ask_user.interactive_pick_option_or_custom", new_callable=AsyncMock) as picker:
        with patch("src.minibot.ui.ask_user._print_user_answer") as print_user:
            picker.return_value = {"kind": "custom", "text": "Warehouse triage"}
            result = await ask_user_question_cli(question)

    assert result == {"answer_text": "Warehouse triage", "selected_option_value": None}
    print_user.assert_called_once_with("Warehouse triage")
