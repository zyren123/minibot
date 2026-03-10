"""Tests for /memory command handler."""

from unittest.mock import MagicMock, patch

import pytest

from src.minibot.ui.cmd_memory import handle_memory_cmd


def _make_memory_manager() -> MagicMock:
    mm = MagicMock()
    mm.read_long_term.return_value = "long-term content"
    mm.read_daily.return_value = "daily content"
    mm.list_daily_files.return_value = ["2026-03-10", "2026-03-09"]
    mm.write_long_term.return_value = "Updated."
    mm.append_long_term.return_value = "Appended."
    return mm


@pytest.mark.asyncio
async def test_memory_show_calls_read_long_term():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("show", mm)
    mm.read_long_term.assert_called_once()


@pytest.mark.asyncio
async def test_memory_list_calls_list_daily():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("list", mm)
    mm.list_daily_files.assert_called_once()


@pytest.mark.asyncio
async def test_memory_append_calls_append_long_term():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_system"):
        await handle_memory_cmd("append hello world", mm)
    mm.append_long_term.assert_called_once_with("hello world")


@pytest.mark.asyncio
async def test_memory_daily_calls_read_daily():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("daily 2026-03-10", mm)
    mm.read_daily.assert_called_once_with("2026-03-10")


@pytest.mark.asyncio
async def test_memory_disabled_prints_warning():
    with patch("src.minibot.ui.cmd_memory.print_system") as mock_print:
        await handle_memory_cmd("show", None)
    mock_print.assert_called_once_with("Memory is disabled.")
