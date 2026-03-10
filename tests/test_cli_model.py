"""Tests for /model command handler."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.minibot.ui.cmd_model import handle_model_cmd


@pytest.fixture
def mock_env(tmp_path: Path) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_BASE_URL=https://api.openai.com/v1\n"
        "OPENAI_API_KEY=sk-test12345\n"
        "MODEL_ID=gpt-4o\n",
        encoding="utf-8"
    )
    return env_file


@pytest.mark.asyncio
async def test_handle_model_cmd_no_args_or_config(mock_env: Path, tmp_path: Path):
    """Test that /model config works and updates .env."""
    selected_item = {"key": "MODEL_ID", "value": "gpt-4o"}
    prompt_fn = AsyncMock(return_value="gpt-4.5")
    
    with patch("src.minibot.ui.cmd_model.interactive_choice", new_callable=AsyncMock) as mock_choice:
        # First return an item, then return None to exit the loop
        mock_choice.side_effect = [selected_item, None]
        
        await handle_model_cmd("config", tmp_path, prompt_fn)
        
        # Verify prompt was called
        prompt_fn.assert_called_once()
        args, kwargs = prompt_fn.call_args
        assert "MODEL_ID" in args[0]
        
        content = mock_env.read_text(encoding="utf-8")
        assert "gpt-4.5" in content
        assert "MODEL_ID" in content
        assert "OPENAI_BASE_URL=https://api.openai.com/v1" in content


@pytest.mark.asyncio
async def test_handle_model_cmd_no_changes(mock_env: Path, tmp_path: Path):
    """Test that identical input doesn't change anything."""
    selected_item = {"key": "MODEL_ID", "value": "gpt-4o"}
    prompt_fn = AsyncMock(return_value="gpt-4o")
    
    with patch("src.minibot.ui.cmd_model.interactive_choice", new_callable=AsyncMock) as mock_choice:
        mock_choice.side_effect = [selected_item, None]
        await handle_model_cmd("config", tmp_path, prompt_fn)
        
        content = mock_env.read_text(encoding="utf-8")
        assert "MODEL_ID=gpt-4o" in content


@pytest.mark.asyncio
async def test_handle_model_cmd_abort_prompt(mock_env: Path, tmp_path: Path):
    """Test KeyboardInterrupt during the text prompt."""
    selected_item = {"key": "MODEL_ID", "value": "gpt-4o"}
    prompt_fn = AsyncMock(side_effect=KeyboardInterrupt)
    
    with patch("src.minibot.ui.cmd_model.interactive_choice", new_callable=AsyncMock) as mock_choice:
        mock_choice.side_effect = [selected_item, None]
        await handle_model_cmd("config", tmp_path, prompt_fn)
        
        content = mock_env.read_text(encoding="utf-8")
        assert "MODEL_ID=gpt-4o" in content


@pytest.mark.asyncio
async def test_handle_model_cmd_missing_env(tmp_path: Path):
    """Test behavior when .env doesn't exist."""
    prompt_fn = AsyncMock()
    with patch("src.minibot.ui.cmd_model.print_system") as mock_print:
        await handle_model_cmd("config", tmp_path, prompt_fn)
        mock_print.assert_called_with(f"Error: {tmp_path / '.env'} does not exist. Please initialize .env first.")


@pytest.mark.asyncio
async def test_handle_model_cmd_invalid_subcommand(tmp_path: Path):
    prompt_fn = AsyncMock()
    with patch("src.minibot.ui.cmd_model._print_usage") as mock_usage:
        await handle_model_cmd("foo", tmp_path, prompt_fn)
        mock_usage.assert_called_once()
