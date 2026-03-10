"""Tests for MCPManager extensions and /mcp command handler."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.minibot.config.schema import MCPConfig, MCPServerConfig
from src.minibot.mcp.manager import MCPManager


def _make_manager(servers: list[MCPServerConfig] | None = None) -> MCPManager:
    if servers is None:
        servers = [
            MCPServerConfig(name="ctx7", transport="stdio", command="npx", enabled=True),
            MCPServerConfig(name="duck", transport="stdio", command="uvx", enabled=False),
        ]
    config = MCPConfig(enabled=True, servers=servers)
    return MCPManager(config, Path("/tmp"))


def test_get_all_server_configs_returns_all():
    mgr = _make_manager()
    configs = mgr.get_all_server_configs()
    assert len(configs) == 2
    names = {c.name for c in configs}
    assert names == {"ctx7", "duck"}


def test_is_connected_false_when_not_connected():
    mgr = _make_manager()
    assert mgr.is_connected("ctx7") is False


@pytest.mark.asyncio
async def test_connect_server_returns_error_for_unknown():
    mgr = _make_manager(servers=[])
    err = await mgr.connect_server("nonexistent")
    assert isinstance(err, ValueError)


@pytest.mark.asyncio
async def test_disconnect_server_no_op_when_not_connected():
    mgr = _make_manager()
    await mgr.disconnect_server("ctx7")  # should not raise
    assert mgr.is_connected("ctx7") is False
