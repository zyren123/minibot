"""In-process agent/session manager for the web server."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..sdk import Minibot
from ..session.manager import SessionManager
from ..skills.loader import SkillLoader

from .bots import BotStore, DEFAULT_BOT_ID
from .plugins import load_tools_from_plugin


class AgentManager:
    def __init__(self, *, workdir: Path | None = None) -> None:
        self._base_config = load_config(workdir=workdir)
        self._bot_store = BotStore(app_home=Path(self._base_config.app_home))

        self._bots: dict[tuple[str, str], Minibot] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._mcp_connected: set[tuple[str, str]] = set()
        self._sessions: dict[str, SessionManager] = {}
        self._global_lock = asyncio.Lock()

        # Server-wide skills sources (shared across bots). Default to repo-local skills/ if present.
        project_skills = Path(self._base_config.project_root) / "skills"
        if project_skills.exists():
            self.skills_dirs: list[str] = [str(project_skills.resolve())]
        else:
            self.skills_dirs = [str(Path(self._base_config.skills_dir).resolve())]

    def config_snapshot(self) -> dict[str, Any]:
        bot_cfg = self.bot_config_snapshot(DEFAULT_BOT_ID)
        return {
            "base_url": bot_cfg.get("base_url"),
            "model": bot_cfg.get("model"),
            "stream_enabled": bot_cfg.get("stream_enabled"),
            "skills_dirs": list(self.skills_dirs),
            "tool_plugins": list(bot_cfg.get("tool_plugins") or []),
            "api_key_masked": bot_cfg.get("api_key_masked"),
        }

    async def update_config(self, patch: dict[str, Any]) -> None:
        """Back-compat server-wide config update (maps to default bot + global skills dirs)."""
        if "skills_dirs" in patch and patch["skills_dirs"] is not None:
            async with self._global_lock:
                self.skills_dirs = [str(Path(p).expanduser().resolve()) for p in patch["skills_dirs"]]

        default_patch: dict[str, Any] = {}
        if "base_url" in patch:
            default_patch["base_url"] = patch.get("base_url")
        if "model" in patch:
            default_patch["model"] = patch.get("model")
        if "api_key" in patch:
            default_patch["api_key"] = patch.get("api_key")
        if "stream_enabled" in patch:
            default_patch["stream_enabled"] = patch.get("stream_enabled")
        if "tool_plugins" in patch:
            default_patch["tool_plugins"] = patch.get("tool_plugins")
        if default_patch:
            await self.update_bot_config(DEFAULT_BOT_ID, default_patch)

    def list_bots(self) -> list[dict[str, Any]]:
        return [
            {"bot_id": b.bot_id, "name": b.name, "is_default": b.is_default}
            for b in self._bot_store.list_bots()
        ]

    async def create_bot(self, *, name: str | None = None) -> dict[str, Any]:
        async with self._global_lock:
            meta = self._bot_store.create_bot(name=name)
            return {"bot_id": meta.bot_id, "name": meta.name, "is_default": meta.is_default}

    async def delete_bot(self, bot_id: str) -> bool:
        async with self._global_lock:
            deleted = self._bot_store.delete_bot(bot_id)
            if deleted:
                self._clear_bot_cache(bot_id)
                self._sessions.pop(bot_id, None)
            return deleted

    def bot_config_snapshot(self, bot_id: str) -> dict[str, Any]:
        cfg, data = self._build_bot_config(bot_id)
        soul_raw = self._bot_store.read_soul(bot_id)
        soul = soul_raw.strip()
        return {
            "bot_id": bot_id,
            "name": str(data.get("name") or ("Default" if bot_id == DEFAULT_BOT_ID else bot_id)),
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "stream_enabled": bool(cfg.llm.stream_enabled),
            "api_key_masked": self._mask_api_key(cfg.llm.api_key),
            "tool_plugins": list(data.get("tool_plugins") or []),
            "skills_disabled": list(data.get("skills_disabled") or []),
            "mcp_overrides": dict(data.get("mcp_overrides") or {}),
            "soul": soul_raw,
        }

    async def update_bot_config(self, bot_id: str, patch: dict[str, Any]) -> None:
        async with self._global_lock:
            bot_patch: dict[str, Any] = {}

            if "name" in patch:
                name = patch.get("name")
                bot_patch["name"] = name.strip() if isinstance(name, str) and name.strip() else None

            if "tool_plugins" in patch:
                raw = patch.get("tool_plugins") or []
                bot_patch["tool_plugins"] = [str(Path(p).expanduser().resolve()) for p in raw]

            if "skills_disabled" in patch:
                raw = patch.get("skills_disabled") or []
                bot_patch["skills_disabled"] = [str(s) for s in raw if str(s).strip()]

            if "mcp_overrides" in patch:
                raw = patch.get("mcp_overrides") or {}
                overrides: dict[str, bool] = {}
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if not isinstance(k, str) or not k.strip():
                            continue
                        if v is None:
                            continue
                        overrides[k] = bool(v)
                bot_patch["mcp_overrides"] = overrides

            llm_fields = {"base_url", "model", "api_key", "stream_enabled"}
            if llm_fields.intersection(patch):
                data = self._bot_store.read_bot_json(bot_id)
                llm = data.get("llm")
                if not isinstance(llm, dict):
                    llm = {}
                for key in llm_fields:
                    if key not in patch:
                        continue
                    value = patch.get(key)
                    if value is None:
                        llm.pop(key, None)
                    else:
                        llm[key] = value
                if llm:
                    bot_patch["llm"] = llm
                else:
                    bot_patch["llm"] = None

            if bot_patch:
                self._bot_store.patch_bot_json(bot_id, bot_patch)

            if "soul" in patch:
                self._bot_store.write_soul(bot_id, str(patch.get("soul") or ""))

            self._clear_bot_cache(bot_id)

    def sessions_for(self, bot_id: str) -> SessionManager:
        existing = self._sessions.get(bot_id)
        if existing is not None:
            return existing
        home = self._bot_store.bot_home(bot_id)
        sessions_dir = (home / "sessions").resolve()
        mgr = SessionManager(sessions_dir)
        self._sessions[bot_id] = mgr
        return mgr

    def skills_snapshot(self) -> list[dict[str, Any]]:
        loader = SkillLoader([Path(p) for p in self.skills_dirs])
        items = [{"name": name, "description": str(meta.get("description") or "")} for name, meta in loader.skills.items()]
        items.sort(key=lambda d: d["name"])
        return items

    def mcp_servers_snapshot(self) -> list[dict[str, Any]]:
        servers: list[dict[str, Any]] = []
        for s in self._base_config.mcp.servers:
            servers.append(
                {
                    "name": s.name,
                    "transport": s.transport,
                    "enabled_default": bool(s.enabled),
                    "command": s.command,
                    "args": list(s.args or []),
                    "url": s.url,
                    "env_keys": sorted(list((s.env or {}).keys())),
                }
            )
        servers.sort(key=lambda d: d["name"])
        return servers

    async def session_lock(self, bot_id: str, session_id: str) -> asyncio.Lock:
        async with self._global_lock:
            key = (bot_id, session_id)
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _build_bot_config(self, bot_id: str) -> tuple[Config, dict[str, Any]]:
        cfg = copy.deepcopy(self._base_config)
        data = self._bot_store.read_bot_json(bot_id)

        home = self._bot_store.bot_home(bot_id)
        cfg.app_home = home
        cfg.session.sessions_dir = str((home / "sessions").resolve())

        llm = data.get("llm")
        if isinstance(llm, dict):
            if llm.get("base_url") is not None:
                cfg.llm.base_url = llm.get("base_url")
            if llm.get("model") is not None:
                cfg.llm.model = llm.get("model")
            if llm.get("api_key") is not None:
                cfg.llm.api_key = llm.get("api_key")
            if llm.get("stream_enabled") is not None:
                cfg.llm.stream_enabled = bool(llm.get("stream_enabled"))

        overrides = data.get("mcp_overrides")
        if isinstance(overrides, dict):
            for server in cfg.mcp.servers:
                value = overrides.get(server.name)
                if value is None:
                    continue
                server.enabled = bool(value)

        return cfg, data

    def _load_plugin_tools(self, plugin_paths: list[str]) -> list[Any]:
        tools: list[Any] = []
        for path in plugin_paths:
            try:
                tools.extend(load_tools_from_plugin(path))
            except Exception:
                # Best-effort: ignore broken plugins for now.
                continue
        return tools

    async def get_bot(self, bot_id: str, session_id: str) -> Minibot:
        key = (bot_id, session_id)
        async with self._global_lock:
            existing = self._bots.get(key)
            if existing is not None:
                return existing

            cfg, data = self._build_bot_config(bot_id)
            plugin_paths = list(data.get("tool_plugins") or [])
            plugin_tools = self._load_plugin_tools(plugin_paths)

            soul = self._bot_store.read_soul(bot_id).strip()
            extra_system_prompt = f"# Soul\n\n{soul}" if soul else None

            skills_disabled = list(data.get("skills_disabled") or [])

            bot = Minibot(
                config=cfg,
                session_id=session_id,
                sessions_dir=cfg.session.sessions_dir,
                skills_dir=self.skills_dirs,
                system_prompt=extra_system_prompt,
                disabled_skills=skills_disabled,
            )
            for tool in plugin_tools:
                try:
                    bot.register_tool(tool)
                except Exception:
                    continue
            bot.agent.set_stream_enabled(bool(cfg.llm.stream_enabled))
            self._bots[key] = bot

        # Best-effort connect MCP once per bot-session.
        if key not in self._mcp_connected:
            try:
                await bot.connect_mcp()
            except Exception:
                pass
            self._mcp_connected.add(key)

        return bot

    async def delete_session(self, bot_id: str, session_id: str) -> bool:
        """Delete session data and clear in-memory caches."""
        key = (bot_id, session_id)
        # Cancel any active generation first.
        bot = self._bots.get(key)
        if bot is not None:
            bot.cancel()
        ok = self.sessions_for(bot_id).delete(session_id)
        async with self._global_lock:
            self._bots.pop(key, None)
            self._locks.pop(key, None)
            self._mcp_connected.discard(key)
        return ok

    def _clear_bot_cache(self, bot_id: str) -> None:
        """Drop all cached sessions for a bot (called under global lock)."""
        keys = [k for k in self._bots.keys() if k[0] == bot_id]
        for key in keys:
            self._bots.pop(key, None)
            self._locks.pop(key, None)
            self._mcp_connected.discard(key)

    @staticmethod
    def _mask_api_key(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 6:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
