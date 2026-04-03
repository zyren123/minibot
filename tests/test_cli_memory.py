"""Tests for /memory command handler."""

from unittest.mock import MagicMock, patch

import pytest

from src.minibot.ui.cmd_memory import handle_memory_cmd


def _make_memory_manager() -> MagicMock:
    mm = MagicMock()
    mm.read.return_value = "memory content"
    mm.search.return_value = []
    return mm


@pytest.mark.asyncio
async def test_memory_boot_reads_system_boot():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("boot", mm)
    mm.read.assert_called_once_with("system://boot")


@pytest.mark.asyncio
async def test_memory_index_reads_system_index():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("index", mm)
    mm.read.assert_called_once_with("system://index")


@pytest.mark.asyncio
async def test_memory_glossary_reads_system_glossary():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("glossary", mm)
    mm.read.assert_called_once_with("system://glossary")


@pytest.mark.asyncio
async def test_memory_read_passes_uri_through():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("read memory://characters/ali", mm)
    mm.read.assert_called_once_with("memory://characters/ali")


@pytest.mark.asyncio
async def test_memory_search_calls_manager_search():
    mm = _make_memory_manager()
    with patch("src.minibot.ui.cmd_memory.print_panel"):
        await handle_memory_cmd("search 白露镇", mm)
    mm.search.assert_called_once_with("白露镇")


@pytest.mark.asyncio
async def test_memory_disabled_prints_warning():
    with patch("src.minibot.ui.cmd_memory.print_system") as mock_print:
        await handle_memory_cmd("show", None)
    mock_print.assert_called_once_with("Memory is disabled.")
