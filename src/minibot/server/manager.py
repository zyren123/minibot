"""In-process agent/session manager for the web server."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..sdk import Minibot
from ..session.manager import SessionManager

from .plugins import load_tools_from_plugin


def _mask_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


class AgentManager:
    def __init__(self, *, workdir: Path | None = None) -> None:
        self._base_config = load_config(workdir=workdir)
        self._bots: dict[str, Minibot] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

        self.sessions = SessionManager(Path(self._base_config.session.sessions_dir))

        self.base_url = self._base_config.llm.base_url
        self.model = self._base_config.llm.model
        self.api_key = self._base_config.llm.api_key
        self.stream_enabled = bool(self._base_config.llm.stream_enabled)
        self.skills_dirs: list[str] = [str(self._base_config.skills_dir)]
        self.tool_plugins: list[str] = []

    def config_snapshot(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "stream_enabled": self.stream_enabled,
            "skills_dirs": list(self.skills_dirs),
            "tool_plugins": list(self.tool_plugins),
            "api_key_masked": _mask_api_key(self.api_key),
        }

    async def update_config(self, patch: dict[str, Any]) -> None:
        async with self._global_lock:
            if "base_url" in patch and patch["base_url"] is not None:
                self.base_url = patch["base_url"]
            if "model" in patch and patch["model"] is not None:
                self.model = patch["model"]
            if "api_key" in patch and patch["api_key"] is not None:
                self.api_key = patch["api_key"]
            if "stream_enabled" in patch and patch["stream_enabled"] is not None:
                self.stream_enabled = bool(patch["stream_enabled"])
            if "skills_dirs" in patch and patch["skills_dirs"] is not None:
                self.skills_dirs = [str(Path(p).expanduser().resolve()) for p in patch["skills_dirs"]]
            if "tool_plugins" in patch and patch["tool_plugins"] is not None:
                self.tool_plugins = [str(Path(p).expanduser().resolve()) for p in patch["tool_plugins"]]

            # Recreate bots on next access to ensure new config is applied consistently.
            self._bots.clear()
            self._locks.clear()

    async def session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    def _build_config(self) -> Config:
        cfg = copy.deepcopy(self._base_config)
        cfg.llm.base_url = self.base_url
        cfg.llm.model = self.model
        cfg.llm.api_key = self.api_key
        cfg.llm.stream_enabled = bool(self.stream_enabled)
        return cfg

    def _load_plugin_tools(self) -> list[Any]:
        tools: list[Any] = []
        for path in self.tool_plugins:
            try:
                tools.extend(load_tools_from_plugin(path))
            except Exception:
                # Best-effort: ignore broken plugins for now.
                continue
        return tools

    async def get_bot(self, session_id: str) -> Minibot:
        async with self._global_lock:
            existing = self._bots.get(session_id)
            if existing is not None:
                return existing

            cfg = self._build_config()
            plugin_tools = self._load_plugin_tools()

            bot = Minibot(
                config=cfg,
                session_id=session_id,
                skills_dir=self.skills_dirs,
            )
            for tool in plugin_tools:
                try:
                    bot.register_tool(tool)
                except Exception:
                    continue
            bot.agent.set_stream_enabled(self.stream_enabled)
            self._bots[session_id] = bot
            return bot
