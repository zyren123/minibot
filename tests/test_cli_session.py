"""Tests for /session command handler."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.minibot.session.manager import SessionManager
from src.minibot.ui.cmd_session import handle_session_cmd


@pytest.fixture
def manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "sessions")


@pytest.mark.asyncio
async def test_session_disabled() -> None:
    with patch("src.minibot.ui.cmd_session.print_system") as mock_print:
        result = await handle_session_cmd("list", None, "test-id")
    assert result is None
    mock_print.assert_called_once_with("Session persistence is disabled.")


@pytest.mark.asyncio
async def test_session_list(manager: SessionManager) -> None:
    manager.create()
    with patch("src.minibot.ui.cmd_session.print_panel"):
        result = await handle_session_cmd("list", manager, "none")
    assert result is None


@pytest.mark.asyncio
async def test_session_new(manager: SessionManager) -> None:
    with patch("src.minibot.ui.cmd_session.print_system"):
        result = await handle_session_cmd("new", manager, "old-id")
    assert result is not None
    assert "switch_to" in result
    assert result["new_history"] == []


@pytest.mark.asyncio
async def test_session_load_by_id(manager: SessionManager) -> None:
    sid = manager.create()
    manager.append_message(sid, {"role": "user", "content": "hi"})
    with patch("src.minibot.ui.cmd_session.print_system"):
        result = await handle_session_cmd(f"load {sid}", manager, "other")
    assert result is not None
    assert result["switch_to"] == sid
    assert len(result["new_history"]) == 1


@pytest.mark.asyncio
async def test_session_load_nonexistent(manager: SessionManager) -> None:
    with patch("src.minibot.ui.cmd_session.print_system") as mock_print:
        result = await handle_session_cmd("load zzzzz", manager, "cur")
    assert result is None
    mock_print.assert_called_once_with("Session 'zzzzz' not found.")


@pytest.mark.asyncio
async def test_session_delete(manager: SessionManager) -> None:
    sid = manager.create()
    with patch("src.minibot.ui.cmd_session.print_system"):
        result = await handle_session_cmd(f"delete {sid}", manager, "other")
    assert result is None
    assert not manager.exists(sid)


@pytest.mark.asyncio
async def test_session_delete_current(manager: SessionManager) -> None:
    sid = manager.create()
    with patch("src.minibot.ui.cmd_session.print_system") as mock_print:
        result = await handle_session_cmd(f"delete {sid}", manager, sid)
    assert result is None
    mock_print.assert_called_once_with("Cannot delete the current active session.")


@pytest.mark.asyncio
async def test_session_info(manager: SessionManager) -> None:
    sid = manager.create()
    manager.append_message(sid, {"role": "user", "content": "hi"})
    with patch("src.minibot.ui.cmd_session.print_panel"):
        result = await handle_session_cmd("info", manager, sid)
    assert result is None


@pytest.mark.asyncio
async def test_session_usage() -> None:
    mgr = MagicMock(spec=SessionManager)
    with patch("src.minibot.ui.cmd_session.print_panel") as mock_panel:
        result = await handle_session_cmd("", mgr, "test")
    assert result is None
    mock_panel.assert_called_once()
