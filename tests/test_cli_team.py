"""Tests for /team command handling."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.minibot.ui.cmd_team import handle_team_cmd


@pytest.mark.asyncio
async def test_handle_team_cmd_persists_toggle_and_updates_agent(tmp_path: Path):
    app_home = tmp_path / ".minibot"
    app_home.mkdir(parents=True, exist_ok=True)
    agent = SimpleNamespace(
        config=SimpleNamespace(app_home=app_home),
        set_team_tools_enabled=AsyncMock(),
    )

    await handle_team_cmd("off", agent, global_enabled=True)
    await handle_team_cmd("on", agent, global_enabled=True)

    bot_json = app_home / "bot.json"
    assert bot_json.exists()
    assert '"teams_enabled": true' in bot_json.read_text(encoding="utf-8")
    assert agent.set_team_tools_enabled.await_args_list[0].kwargs == {"enabled": False}
    assert agent.set_team_tools_enabled.await_args_list[1].kwargs == {"enabled": True}


@pytest.mark.asyncio
async def test_handle_team_cmd_respects_global_upper_bound(tmp_path: Path):
    app_home = tmp_path / ".minibot"
    app_home.mkdir(parents=True, exist_ok=True)
    agent = SimpleNamespace(
        config=SimpleNamespace(app_home=app_home),
        set_team_tools_enabled=AsyncMock(),
    )

    with patch("src.minibot.ui.cmd_team.print_system") as mock_print:
        await handle_team_cmd("on", agent, global_enabled=False)

    assert '"teams_enabled": true' in (app_home / "bot.json").read_text(encoding="utf-8")
    agent.set_team_tools_enabled.assert_awaited_once_with(enabled=False)
    assert any("global config" in str(call.args[0]).lower() for call in mock_print.call_args_list)
