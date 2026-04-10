"""In-process agent/session manager for the web server."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import re
import shutil
from typing import Any, AsyncIterator
import uuid

from openai import AsyncOpenAI

from ..config import Config, load_config
from ..config.writer import update_default_config, update_mcp_server_config, upsert_env_value
from ..memory.manager import MemoryManager
from ..mcp.manager import MCPManager
from ..sdk import Minibot
from ..session.manager import SessionManager
from ..skills.loader import SkillLoader
from ..subagents.registry import AgentType

from .bots import BotStore, DEFAULT_BOT_ID
from .dashboard_store import DashboardStore
from .platform_runtime import FeishuPlatformRuntime
from .platforms import (
    ConversationMapStore,
    PlatformStore,
    SUPPORTED_PLATFORM_KINDS,
    platform_kind_label,
)
from .plugins import load_tools_from_plugin


_STATE_UNSET = object()


class AgentManager:
    _MAX_SKILL_NAME_LENGTH = 64
    _ALLOWED_SKILL_RESOURCES = ("scripts", "references", "assets")

    @staticmethod
    def _display_bot_name(bot_id: str, data: dict[str, Any]) -> str:
        raw_name = data.get("name")
        if bot_id == DEFAULT_BOT_ID:
            if isinstance(raw_name, str) and raw_name.strip() and raw_name.strip() != "Default":
                return raw_name.strip()
            return "Minibot"
        return str(raw_name or bot_id)

    @staticmethod
    def _auto_compact_threshold_tokens(max_context_tokens: int) -> int:
        threshold = int(max_context_tokens * 0.8)
        if threshold > 0:
            return threshold
        return max(max_context_tokens, 1)

    def __init__(self, *, workdir: Path | None = None) -> None:
        self._base_config = load_config(workdir=workdir)
        self.app_home = Path(self._base_config.app_home).resolve()
        self._bot_store = BotStore(app_home=self.app_home)
        self._dashboard_store = DashboardStore(app_home=self.app_home)
        self._platform_store = PlatformStore(app_home=self.app_home)
        self._default_config_path = (self.app_home / "config" / "default.yaml").resolve()
        self._env_path = (self.app_home / ".env").resolve()
        self._mcp_config_path = (self.app_home / "config" / "mcp_servers.yaml").resolve()
        self.user_skills_dir = Path(self._base_config.skills_dir).resolve()
        self.project_skills_dir = (Path(self._base_config.project_root) / "skills").resolve()

        self._bots: dict[tuple[str, str], Minibot] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._mcp_connected: set[tuple[str, str]] = set()
        self._admin_mcp_manager: MCPManager | None = None
        self._sessions: dict[str, SessionManager] = {}
        self._platform_runtimes: dict[str, Any] = {}
        self._platform_runtime_state: dict[str, dict[str, Any]] = {}
        self._platforms_started = False
        self._global_lock = asyncio.Lock()

        default_dirs = [str(self.project_skills_dir), str(self.user_skills_dir)]
        self.skills_dirs = list(dict.fromkeys(default_dirs))

    def config_snapshot(self) -> dict[str, Any]:
        bot_cfg = self.bot_config_snapshot(DEFAULT_BOT_ID)
        memory_database_url = self._base_config.memory.database_url
        return {
            "base_url": bot_cfg.get("base_url"),
            "model": bot_cfg.get("model"),
            "stream_enabled": bot_cfg.get("stream_enabled"),
            "skills_dirs": list(self.skills_dirs),
            "user_skills_dir": str(self.user_skills_dir),
            "project_skills_dir": str(self.project_skills_dir),
            "default_skill_target": "user",
            "available_skill_targets": ["user", "project"],
            "tool_plugins": list(bot_cfg.get("tool_plugins") or []),
            "api_key_masked": bot_cfg.get("api_key_masked"),
            "memory_backend": self._base_config.memory.backend,
            "memory_database_url_configured": bool(memory_database_url),
            "memory_database_url_value": memory_database_url,
        }

    async def update_config(self, patch: dict[str, Any]) -> None:
        """Back-compat server-wide config update (maps to default bot + global skills dirs)."""
        async with self._global_lock:
            if "skills_dirs" in patch and patch["skills_dirs"] is not None:
                self.skills_dirs = [str(Path(p).expanduser().resolve()) for p in patch["skills_dirs"]]

            requested_backend = patch.get("memory_backend")
            backend = (
                str(requested_backend).strip().lower()
                if requested_backend is not None
                else self._base_config.memory.backend
            )
            if backend not in {"sqlite", "postgres"}:
                raise ValueError("memory_backend must be 'sqlite' or 'postgres'")

            raw_memory_url = patch.get("memory_database_url_value", _STATE_UNSET)
            next_memory_url = self._base_config.memory.database_url
            if raw_memory_url is not _STATE_UNSET:
                if isinstance(raw_memory_url, str):
                    next_memory_url = raw_memory_url.strip() or None
                else:
                    next_memory_url = None

            if backend == "postgres" and not next_memory_url:
                raise ValueError("memory_database_url_value is required when memory_backend=postgres")

            if requested_backend is not None:
                update_default_config(
                    self._default_config_path,
                    {
                        "memory": {
                            "backend": backend,
                            "database_url": "${MEMORY_DATABASE_URL}",
                        }
                    },
                )
                self._base_config.memory.backend = backend

            if raw_memory_url is not _STATE_UNSET:
                upsert_env_value(self._env_path, "MEMORY_DATABASE_URL", next_memory_url)
                self._base_config.memory.database_url = next_memory_url

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

    def dashboard_snapshot(self) -> dict[str, Any]:
        snapshot = self._dashboard_store.snapshot()
        return {
            "providers": snapshot["providers"],
            "models": snapshot["models"],
            "bots": self.list_bots(),
            "platforms": self.list_platforms(),
            "available_models": self.active_models_snapshot(),
        }

    def active_models_snapshot(self) -> list[dict[str, Any]]:
        return self._dashboard_store.active_model_options()

    def list_bots(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for meta in self._bot_store.list_bots():
            cfg = self.bot_config_snapshot(meta.bot_id)
            out.append(
                {
                    "bot_id": meta.bot_id,
                    "name": cfg["name"],
                    "is_default": meta.is_default,
                    "enabled": cfg["enabled"],
                    "subagent_exposable": cfg["subagent_exposable"],
                    "subagent_name": cfg["subagent_name"],
                    "subagent_description": cfg["subagent_description"],
                    "attached_subagent_bot_ids": cfg["attached_subagent_bot_ids"],
                    "chat_model_id": cfg["chat_model_id"],
                    "chat_ready": cfg["chat_ready"],
                    "chat_disabled_reason": cfg["chat_disabled_reason"],
                }
            )
        return out

    async def create_bot(self, *, name: str | None = None) -> dict[str, Any]:
        async with self._global_lock:
            meta = self._bot_store.create_bot(name=name)
            return {
                "bot_id": meta.bot_id,
                "name": meta.name,
                "is_default": meta.is_default,
                "enabled": True,
                "subagent_exposable": False,
                "subagent_name": None,
                "subagent_description": None,
                "attached_subagent_bot_ids": [],
                "chat_model_id": None,
                "chat_ready": True,
                "chat_disabled_reason": None,
            }

    async def delete_bot(self, bot_id: str) -> bool:
        rebound_platform_ids: list[str] = []
        async with self._global_lock:
            referencing = set(self._bots_referencing(bot_id))
            rebound_platform_ids = self._platform_store.rebind_bot(
                source_bot_id=bot_id,
                target_bot_id=DEFAULT_BOT_ID,
            )
            deleted = self._bot_store.delete_bot(bot_id)
            if deleted:
                self._clear_bot_cache(bot_id)
                for owner_bot_id in referencing:
                    self._clear_bot_cache(owner_bot_id)
                self._sessions.pop(bot_id, None)
        for platform_id in rebound_platform_ids:
            ConversationMapStore(app_home=self.app_home, platform_id=platform_id).clear()
            await self._sync_platform_runtime(platform_id)
        return deleted

    def bot_config_snapshot(self, bot_id: str) -> dict[str, Any]:
        cfg, data = self._build_bot_config(bot_id)
        soul_raw = self._bot_store.read_soul(bot_id)
        chat_state = self._bot_chat_state(bot_id, data)
        return {
            "bot_id": bot_id,
            "name": self._display_bot_name(bot_id, data),
            "enabled": self._bot_enabled(data),
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "chat_model_id": data.get("chat_model_id"),
            "max_context_tokens": int(cfg.llm.max_context_tokens),
            "auto_compact_threshold_tokens": self._auto_compact_threshold_tokens(int(cfg.llm.max_context_tokens)),
            "stream_enabled": bool(cfg.llm.stream_enabled),
            "teams_enabled": bool(data.get("teams_enabled", True)),
            "api_key_masked": self._mask_api_key(cfg.llm.api_key),
            "tool_plugins": list(data.get("tool_plugins") or []),
            "skills_disabled": list(data.get("skills_disabled") or []),
            "mcp_overrides": dict(data.get("mcp_overrides") or {}),
            "active_memory_namespace": data.get("active_memory_namespace"),
            "soul": soul_raw,
            "subagent_exposable": bool(data.get("subagent_exposable", False)),
            "subagent_name": data.get("subagent_name"),
            "subagent_description": data.get("subagent_description"),
            "attached_subagent_bot_ids": list(data.get("attached_subagent_bot_ids") or []),
            "chat_ready": chat_state["ready"],
            "chat_disabled_reason": chat_state["reason"],
        }

    async def update_bot_config(self, bot_id: str, patch: dict[str, Any]) -> None:
        async with self._global_lock:
            referenced_before = set(self._bots_referencing(bot_id))
            bot_patch: dict[str, Any] = {}

            if "name" in patch:
                bot_patch["name"] = self._optional_text(patch.get("name"))

            if "enabled" in patch and patch.get("enabled") is not None:
                bot_patch["enabled"] = bool(patch.get("enabled"))

            if "subagent_exposable" in patch and patch.get("subagent_exposable") is not None:
                bot_patch["subagent_exposable"] = bool(patch.get("subagent_exposable"))

            if "subagent_name" in patch:
                bot_patch["subagent_name"] = self._optional_text(patch.get("subagent_name"))

            if "subagent_description" in patch:
                bot_patch["subagent_description"] = self._optional_text(patch.get("subagent_description"))

            if "chat_model_id" in patch:
                raw_model_id = self._optional_text(patch.get("chat_model_id"))
                if raw_model_id is None:
                    bot_patch["chat_model_id"] = None
                else:
                    if self._dashboard_store.model_option(raw_model_id) is None:
                        raise ValueError("Selected chat model is disabled or unavailable")
                    bot_patch["chat_model_id"] = raw_model_id

            if "attached_subagent_bot_ids" in patch:
                bot_patch["attached_subagent_bot_ids"] = self._normalize_attached_subagents(
                    owner_bot_id=bot_id,
                    raw=patch.get("attached_subagent_bot_ids"),
                )

            if "teams_enabled" in patch and patch.get("teams_enabled") is not None:
                bot_patch["teams_enabled"] = bool(patch.get("teams_enabled"))

            if "tool_plugins" in patch:
                raw = patch.get("tool_plugins") or []
                bot_patch["tool_plugins"] = [str(Path(p).expanduser().resolve()) for p in raw]

            if "active_memory_namespace" in patch:
                bot_patch["active_memory_namespace"] = self._optional_text(patch.get("active_memory_namespace"))

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
                bot_patch["llm"] = llm or None

            if bot_patch:
                self._bot_store.patch_bot_json(bot_id, bot_patch)

            if "soul" in patch:
                self._bot_store.write_soul(bot_id, str(patch.get("soul") or ""))

            affected_bot_ids = {bot_id} | referenced_before | set(self._bots_referencing(bot_id))
            if "attached_subagent_bot_ids" in bot_patch:
                affected_bot_ids.add(bot_id)
            for affected_bot_id in affected_bot_ids:
                self._clear_bot_cache(affected_bot_id)

    def list_provider_models(self, provider_id: str) -> list[dict[str, Any]]:
        return [item for item in self._dashboard_store.list_models() if item.get("provider_id") == provider_id]

    def create_provider(self, patch: dict[str, Any]) -> dict[str, Any]:
        return self._dashboard_store.create_provider(
            name=str(patch.get("name") or "").strip(),
            base_url=str(patch.get("base_url") or "").strip(),
            api_key=self._optional_text(patch.get("api_key")),
            enabled=bool(patch.get("enabled", True)),
        )

    def list_platforms(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in self._platform_store.list_platforms():
            runtime_state = self._platform_runtime_state_for(str(item["platform_id"]))
            bound_bot_id = str(item.get("bound_bot_id") or DEFAULT_BOT_ID)
            if not self.bot_exists(bound_bot_id):
                bound_bot_id = DEFAULT_BOT_ID
            bound_bot_name = self._display_bot_name(bound_bot_id, self._bot_store.read_bot_json(bound_bot_id))
            out.append(
                {
                    **item,
                    "bound_bot_id": bound_bot_id,
                    "bound_bot_name": bound_bot_name,
                    "connected": bool(runtime_state["connected"]),
                    "last_error": runtime_state["last_error"],
                    "last_event_at": runtime_state["last_event_at"],
                }
            )
        return out

    def platform_snapshot(self, platform_id: str) -> dict[str, Any]:
        for item in self.list_platforms():
            if item["platform_id"] == platform_id:
                return item
        raise KeyError(platform_id)

    async def create_platform(self, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._global_lock:
            name = self._optional_text(patch.get("name"))
            if not name:
                raise ValueError("Platform name is required")
            kind = str(patch.get("kind") or "").strip().lower()
            if kind not in SUPPORTED_PLATFORM_KINDS:
                raise ValueError(f"Unsupported platform kind: {kind or 'unknown'}")
            bound_bot_id = str(patch.get("bound_bot_id") or DEFAULT_BOT_ID).strip() or DEFAULT_BOT_ID
            if not self.bot_exists(bound_bot_id):
                raise ValueError("Bound bot not found")
            app_id = self._optional_text(patch.get("app_id"))
            if kind == "feishu" and not app_id:
                raise ValueError("Feishu app_id is required")
            app_secret = self._optional_text(patch.get("app_secret"))
            if kind == "feishu" and not app_secret:
                raise ValueError("Feishu app_secret is required")

            created = self._platform_store.create_platform(
                name=name,
                kind=kind,
                enabled=bool(patch.get("enabled", True)),
                bound_bot_id=bound_bot_id,
                app_id=app_id or "",
                app_secret=app_secret or "",
            )

        await self._sync_platform_runtime(str(created["platform_id"]))
        return self.platform_snapshot(str(created["platform_id"]))

    async def update_platform(self, platform_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        bound_bot_changed = False
        store_patch: dict[str, Any] = {}

        async with self._global_lock:
            existing = self._platform_store.platform_for_update(platform_id)

            if "name" in patch:
                name = self._optional_text(patch.get("name"))
                if not name:
                    raise ValueError("Platform name is required")
                store_patch["name"] = name

            if "enabled" in patch and patch.get("enabled") is not None:
                store_patch["enabled"] = bool(patch.get("enabled"))

            if "bound_bot_id" in patch:
                bound_bot_id = str(patch.get("bound_bot_id") or DEFAULT_BOT_ID).strip() or DEFAULT_BOT_ID
                if not self.bot_exists(bound_bot_id):
                    raise ValueError("Bound bot not found")
                store_patch["bound_bot_id"] = bound_bot_id
                bound_bot_changed = bound_bot_id != str(existing.get("bound_bot_id") or DEFAULT_BOT_ID)

            if "app_id" in patch:
                app_id = self._optional_text(patch.get("app_id"))
                if not app_id:
                    raise ValueError("Feishu app_id is required")
                store_patch["app_id"] = app_id

            if "app_secret" in patch:
                app_secret = self._optional_text(patch.get("app_secret"))
                if app_secret is None:
                    raise ValueError("Feishu app_secret is required")
                store_patch["app_secret"] = app_secret

            if store_patch:
                self._platform_store.update_platform(platform_id, store_patch)

        if bound_bot_changed:
            ConversationMapStore(app_home=self.app_home, platform_id=platform_id).clear()

        await self._sync_platform_runtime(platform_id)
        return self.platform_snapshot(platform_id)

    async def delete_platform(self, platform_id: str) -> bool:
        await self._stop_platform_runtime(platform_id, clear_state=True)
        async with self._global_lock:
            deleted = self._platform_store.delete_platform(platform_id)
            if deleted:
                ConversationMapStore(app_home=self.app_home, platform_id=platform_id).delete_storage()
                self._platform_runtime_state.pop(platform_id, None)
            return deleted

    def update_provider(self, provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        updated = self._dashboard_store.update_provider(provider_id, patch)
        for bot_id in self._bot_ids_using_provider(provider_id):
            self._clear_bot_cache(bot_id)
        return updated

    async def start_platform_runtimes(self) -> None:
        async with self._global_lock:
            self._platforms_started = True
            platform_ids = self._platform_store.all_platform_ids()
        for platform_id in platform_ids:
            await self._sync_platform_runtime(platform_id)

    async def stop_platform_runtimes(self) -> None:
        async with self._global_lock:
            self._platforms_started = False
            platform_ids = list(self._platform_runtimes.keys())
        for platform_id in platform_ids:
            await self._stop_platform_runtime(platform_id, clear_state=False)

    async def fetch_provider_models(self, provider_id: str) -> list[dict[str, Any]]:
        provider = self._dashboard_store.provider_credentials(provider_id)
        base_url = str(provider.get("base_url") or "").strip()
        api_key = provider.get("api_key")
        if not base_url:
            raise ValueError("Provider base URL is required")
        if not api_key:
            raise ValueError("Provider API key is required to fetch models")
        names = await self._fetch_provider_model_names(base_url=base_url, api_key=api_key)
        existing = {
            item["model_name"]
            for item in self.list_provider_models(provider_id)
        }
        return [
            {"model_name": name, "already_added": name in existing}
            for name in sorted(names)
        ]

    def create_models_for_provider(self, provider_id: str, patch: dict[str, Any]) -> list[dict[str, Any]]:
        raw_names = patch.get("model_names") or []
        names = [str(item).strip() for item in raw_names if str(item).strip()]
        return self._dashboard_store.create_models(
            provider_id=provider_id,
            model_names=names,
            added_via=str(patch.get("added_via") or "manual"),
        )

    def delete_models_for_provider(self, provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        raw_ids = patch.get("model_ids") or []
        model_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        deleted_model_ids = self._dashboard_store.delete_models(provider_id=provider_id, model_ids=model_ids)
        affected_bot_ids: set[str] = set()
        for model_id in deleted_model_ids:
            affected_bot_ids.update(self._bot_ids_using_model(model_id))
        for bot_id in affected_bot_ids:
            self._clear_bot_cache(bot_id)
        return {
            "deleted_model_ids": deleted_model_ids,
            "deleted_count": len(deleted_model_ids),
        }

    def update_model(self, model_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        updated = self._dashboard_store.update_model(model_id, patch)
        for bot_id in self._bot_ids_using_model(model_id):
            self._clear_bot_cache(bot_id)
        return updated

    def subagent_candidates(self, bot_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for meta in self._bot_store.list_bots():
            if meta.bot_id == bot_id:
                continue
            data = self._bot_store.read_bot_json(meta.bot_id)
            if not self._bot_enabled(data):
                continue
            if not bool(data.get("subagent_exposable", False)):
                continue
            out.append(
                {
                    "bot_id": meta.bot_id,
                    "name": str(data.get("name") or meta.name or meta.bot_id),
                    "subagent_name": self._effective_subagent_name(meta.bot_id, data),
                    "subagent_description": self._effective_subagent_description(meta.bot_id, data),
                    "enabled": True,
                }
            )
        out.sort(key=lambda item: str(item["subagent_name"]).lower())
        return out

    def sessions_for(self, bot_id: str) -> SessionManager:
        existing = self._sessions.get(bot_id)
        if existing is not None:
            return existing
        home = self._bot_store.bot_home(bot_id)
        sessions_dir = (home / "sessions").resolve()
        mgr = SessionManager(sessions_dir)
        self._sessions[bot_id] = mgr
        return mgr

    def session_messages(self, bot_id: str, session_id: str) -> list[dict[str, Any]]:
        session_mgr = self.sessions_for(bot_id)
        if not session_mgr.exists(session_id):
            raise ValueError("Session not found")
        return self._serialize_messages(session_mgr.load(session_id))

    @staticmethod
    def _normalize_pending_question_answer(
        question: dict[str, Any],
        *,
        answer_text: Any,
        selected_option_value: Any,
    ) -> tuple[str, str | None]:
        normalized_text = str(answer_text or "").strip()
        normalized_selected = (
            str(selected_option_value).strip()
            if selected_option_value is not None and str(selected_option_value).strip()
            else None
        )
        option_values = {str(item.get("value")) for item in question.get("options") or []}

        if normalized_selected is not None and normalized_selected not in option_values:
            raise ValueError("Pending question selected option is invalid")
        if not question.get("allow_free_text", True) and normalized_selected is None:
            raise ValueError("Pending question requires choosing one of the provided options")
        if question.get("required", True) and not normalized_text and normalized_selected is None:
            raise ValueError("Pending question requires a non-empty answer")

        if normalized_selected is not None and not normalized_text:
            for item in question.get("options") or []:
                if item.get("value") == normalized_selected:
                    normalized_text = str(item.get("label") or normalized_selected)
                    break

        return normalized_text, normalized_selected

    def _reconcile_stale_runtime(
        self,
        bot_id: str,
        session_id: str,
        runtime_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if runtime_state is None or runtime_state.get("state") != "streaming":
            return runtime_state

        session_mgr = self.sessions_for(bot_id)
        assistant_message_id = runtime_state.get("assistant_message_id")
        if isinstance(assistant_message_id, str) and assistant_message_id.strip():
            session_mgr.update_message(
                session_id,
                assistant_message_id,
                lambda current: (
                    dict(current)
                    if current.get("completion_state") == "complete"
                    else {**current, "completion_state": "interrupted"}
                ),
            )
        session_mgr.delete_runtime_state(session_id)
        return None

    async def pending_question_snapshot(self, bot_id: str, session_id: str) -> dict[str, Any] | None:
        session_mgr = self.sessions_for(bot_id)
        runtime_state = session_mgr.load_runtime_state(session_id)
        runtime_state = self._reconcile_stale_runtime(bot_id, session_id, runtime_state)
        if runtime_state is not None and runtime_state.get("state") == "awaiting_user_answer":
            pending_question = runtime_state.get("pending_question")
            if isinstance(pending_question, dict) and pending_question:
                return dict(pending_question)

        bot = await self.get_bot(bot_id, session_id)
        if not hasattr(bot, "pending_question"):
            raise ValueError("Bot does not support askuserquestion state")
        question = bot.pending_question()
        if not isinstance(question, dict) or not question:
            return None
        return dict(question)

    async def submit_user_answer(self, bot_id: str, session_id: str, payload: dict[str, Any]) -> None:
        bot = await self.get_bot(bot_id, session_id)
        if not hasattr(bot, "submit_user_answer"):
            raise ValueError("Bot does not support askuserquestion answers")
        await bot.submit_user_answer(
            question_id=str(payload.get("question_id") or ""),
            answer_text=str(payload.get("answer_text") or ""),
            selected_option_value=(
                str(payload.get("selected_option_value")).strip()
                if payload.get("selected_option_value") is not None
                and str(payload.get("selected_option_value")).strip()
                else None
            ),
        )

    async def submit_user_answer_stream(
        self,
        bot_id: str,
        session_id: str,
        payload: dict[str, Any],
        *,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session_mgr = self.sessions_for(bot_id)
        runtime_state = self._reconcile_stale_runtime(
            bot_id,
            session_id,
            session_mgr.load_runtime_state(session_id),
        )
        if runtime_state is None or runtime_state.get("state") != "awaiting_user_answer":
            raise ValueError("No persisted pending question is waiting for an answer")

        pending_question = runtime_state.get("pending_question")
        if not isinstance(pending_question, dict) or not pending_question:
            raise ValueError("Persisted pending question state is invalid")

        question_id = str(payload.get("question_id") or "").strip()
        if question_id != str(pending_question.get("question_id") or "").strip():
            raise ValueError("Pending question id does not match")

        answer_text, selected_option_value = self._normalize_pending_question_answer(
            pending_question,
            answer_text=payload.get("answer_text"),
            selected_option_value=payload.get("selected_option_value"),
        )

        assistant_message_id = str(
            runtime_state.get("assistant_message_id")
            or pending_question.get("message_id")
            or ""
        ).strip()
        if not assistant_message_id:
            raise ValueError("Persisted pending question is missing its assistant message id")

        session_mgr.update_message(
            session_id,
            assistant_message_id,
            lambda current: {**current, "question_pending": False},
        )
        session_mgr.append_message(
            session_id,
            {
                "role": "user",
                "content": answer_text,
                "message_id": f"msg-{uuid.uuid4().hex[:12]}",
                "answer_to_question_id": question_id,
                "selected_option_value": selected_option_value,
            },
        )
        session_mgr.delete_runtime_state(session_id)
        self._clear_session_cache(bot_id, session_id, preserve_lock=True)

        bot = await self.get_bot(bot_id, session_id)
        if hasattr(bot, "resume_pending_question_stream"):
            async for event in bot.resume_pending_question_stream(
                question_id=question_id,
                answer_text=answer_text,
                selected_option_value=selected_option_value,
                reasoning_effort=reasoning_effort,
            ):
                yield event
            return

        if not hasattr(bot, "stream_existing_messages"):
            raise ValueError("Bot does not support persisted pending question resume")

        async for event in bot.stream_existing_messages(reasoning_effort=reasoning_effort):
            yield event

    async def delete_message_turn(self, bot_id: str, session_id: str, message_id: str) -> dict[str, Any]:
        session_mgr = self.sessions_for(bot_id)
        if not session_mgr.exists(session_id):
            raise ValueError("Session not found")
        messages = session_mgr.load(session_id)
        start, end, _user_index = self._assistant_turn_bounds(messages, message_id)
        deleted_message_ids = [
            str(item.get("message_id"))
            for item in messages[start:end]
            if isinstance(item.get("message_id"), str) and str(item.get("message_id")).strip()
        ]
        remaining = messages[:start] + messages[end:]
        session_mgr.overwrite(session_id, remaining)
        session_mgr.delete_runtime_state(session_id)
        self._clear_session_cache(bot_id, session_id, preserve_lock=True)
        return {
            "session_id": session_id,
            "messages": self._serialize_messages(remaining),
            "deleted_message_ids": deleted_message_ids,
        }

    def prepare_regenerate_message_turn(self, bot_id: str, session_id: str, message_id: str) -> str:
        session_mgr = self.sessions_for(bot_id)
        if not session_mgr.exists(session_id):
            raise ValueError("Session not found")
        messages = session_mgr.load(session_id)
        start, end, user_index = self._assistant_turn_bounds(messages, message_id)
        if end != len(messages):
            raise ValueError("Only the latest assistant turn can be regenerated")
        user_message = messages[user_index]
        prompt = str(user_message.get("content") or "").strip()
        if not prompt:
            raise ValueError("Cannot regenerate an empty user message")

        preserved = messages[:start]
        session_mgr.overwrite(session_id, preserved)
        session_mgr.delete_runtime_state(session_id)
        self._clear_session_cache(bot_id, session_id, preserve_lock=True)
        return prompt

    async def regenerate_message_turn(self, bot_id: str, session_id: str, message_id: str) -> dict[str, Any]:
        prompt = self.prepare_regenerate_message_turn(bot_id, session_id, message_id)
        bot = await self.get_bot(bot_id, session_id)
        result = await bot.chat(prompt, session_id=session_id)
        return {
            "session_id": session_id,
            "messages": self._serialize_messages(result.messages),
            "regenerated_from_message_id": message_id,
        }

    def skills_snapshot(self) -> list[dict[str, Any]]:
        records = self._skill_records()
        records.sort(key=lambda item: (item["name"], item["source_type"], item["resolved_path"]))
        return records

    async def create_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._global_lock:
            raw_name = str(payload.get("name") or "").strip()
            if not raw_name:
                raise ValueError("Skill name is required")

            skill_name = self._normalize_skill_name(raw_name)
            if not skill_name:
                raise ValueError("Skill name must include at least one letter or digit")
            if len(skill_name) > self._MAX_SKILL_NAME_LENGTH:
                raise ValueError(
                    f"Skill name '{skill_name}' is too long ({len(skill_name)} characters). "
                    f"Maximum is {self._MAX_SKILL_NAME_LENGTH} characters."
                )

            scope = str(payload.get("scope") or "user").strip().lower()
            if scope not in {"user", "project"}:
                raise ValueError("Skill scope must be 'user' or 'project'")

            target_root = self._skill_target_dir(scope)
            target_root.mkdir(parents=True, exist_ok=True)
            skill_dir = target_root / skill_name
            if skill_dir.exists():
                raise ValueError(f"Skill directory already exists: {skill_dir}")

            description = self._optional_text(payload.get("description"))
            skill_dir.mkdir(parents=True, exist_ok=False)
            (skill_dir / "SKILL.md").write_text(
                self._skill_template(skill_name, description),
                encoding="utf-8",
            )

            self._ensure_active_skill_dir(scope)
            created = self._find_skill_record(scope=scope, folder_name=skill_dir.name)
            if created is None:
                raise ValueError("Created skill could not be resolved")
            return created

    async def delete_skill(self, *, scope: str, folder_name: str) -> dict[str, Any]:
        async with self._global_lock:
            normalized_scope = str(scope or "").strip().lower()
            if normalized_scope != "user":
                raise ValueError("Only user skills can be deleted from the UI")
            if not folder_name or Path(folder_name).name != folder_name or folder_name in {".", ".."}:
                raise ValueError("Invalid skill folder name")

            target_dir = self.user_skills_dir / folder_name
            skill_md = target_dir / "SKILL.md"
            if not skill_md.exists():
                raise ValueError("Skill not found")

            loader = SkillLoader([self.user_skills_dir])
            parsed = loader.parse_skill_md(skill_md)
            if not parsed:
                raise ValueError("Skill metadata is invalid")

            shutil.rmtree(target_dir)
            self._bot_store.remove_skill_references(str(parsed.get("name") or folder_name))
            for bot_id in self._bot_store.all_bot_ids():
                self._clear_bot_cache(bot_id)

            return {
                "deleted": True,
                "skill_name": str(parsed.get("name") or folder_name),
                "scope": normalized_scope,
                "folder_name": folder_name,
            }

    async def _ensure_admin_mcp_manager(self) -> MCPManager:
        async with self._global_lock:
            if self._admin_mcp_manager is not None:
                return self._admin_mcp_manager
            manager = MCPManager(copy.deepcopy(self._base_config.mcp), self._base_config.workdir)
            await manager.connect_all()
            self._admin_mcp_manager = manager
            return manager

    async def mcp_servers_snapshot(self) -> list[dict[str, Any]]:
        manager = await self._ensure_admin_mcp_manager()
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
                    "connected": manager.is_connected(s.name),
                }
            )
        servers.sort(key=lambda d: d["name"])
        return servers

    async def update_mcp_server(self, server_name: str, patch: dict[str, Any]) -> dict[str, Any]:
        manager = await self._ensure_admin_mcp_manager()
        async with self._global_lock:
            cfg = next((item for item in self._base_config.mcp.servers if item.name == server_name), None)
            if cfg is None:
                raise ValueError(f"Unknown MCP server: {server_name}")

            update_payload: dict[str, Any] = {}
            if "enabled_default" in patch and patch.get("enabled_default") is not None:
                update_payload["enabled"] = bool(patch.get("enabled_default"))
            if cfg.transport == "stdio":
                if "command" in patch:
                    command = self._optional_text(patch.get("command"))
                    update_payload["command"] = command
                if "args" in patch:
                    raw_args = patch.get("args") or []
                    update_payload["args"] = [str(item).strip() for item in raw_args if str(item).strip()]
            elif cfg.transport == "sse":
                if "url" in patch:
                    url = self._optional_text(patch.get("url"))
                    update_payload["url"] = url
            else:
                if "command" in patch:
                    update_payload["command"] = self._optional_text(patch.get("command"))
                if "args" in patch:
                    raw_args = patch.get("args") or []
                    update_payload["args"] = [str(item).strip() for item in raw_args if str(item).strip()]
                if "url" in patch:
                    update_payload["url"] = self._optional_text(patch.get("url"))

            persisted = update_mcp_server_config(self._mcp_config_path, server_name, update_payload)

            cfg.enabled = bool(persisted.get("enabled", cfg.enabled))
            cfg.command = persisted.get("command")
            cfg.url = persisted.get("url")
            cfg.args = list(persisted.get("args") or [])

            runtime_cfg = next((item for item in manager.config.servers if item.name == server_name), None)
            if runtime_cfg is None:
                raise ValueError(f"Unknown MCP server: {server_name}")
            runtime_cfg.enabled = cfg.enabled
            runtime_cfg.command = cfg.command
            runtime_cfg.url = cfg.url
            runtime_cfg.args = list(cfg.args or [])

            if manager.is_connected(server_name):
                await manager.disconnect_server(server_name)
            if self._base_config.mcp.enabled and cfg.enabled:
                await manager.connect_server(server_name)

            for bot_id in self._bot_store.all_bot_ids():
                self._clear_bot_cache(bot_id)

        snapshot = await self.mcp_servers_snapshot()
        for server in snapshot:
            if server["name"] == server_name:
                return server
        raise ValueError(f"Unknown MCP server: {server_name}")

    async def connect_mcp_server(self, server_name: str) -> dict[str, Any]:
        manager = await self._ensure_admin_mcp_manager()
        async with self._global_lock:
            cfg = next((item for item in self._base_config.mcp.servers if item.name == server_name), None)
            if cfg is None:
                raise ValueError(f"Unknown MCP server: {server_name}")
            runtime_cfg = next((item for item in manager.config.servers if item.name == server_name), None)
            if runtime_cfg is None:
                raise ValueError(f"Unknown MCP server: {server_name}")
            runtime_cfg.command = cfg.command
            runtime_cfg.url = cfg.url
            runtime_cfg.args = list(cfg.args or [])
            err = await manager.connect_server(server_name)
            if err is not None:
                raise ValueError(str(err))
            for bot_id in self._bot_store.all_bot_ids():
                self._clear_bot_cache(bot_id)
        snapshot = await self.mcp_servers_snapshot()
        for server in snapshot:
            if server["name"] == server_name:
                return server
        raise ValueError(f"Unknown MCP server: {server_name}")

    async def disconnect_mcp_server(self, server_name: str) -> dict[str, Any]:
        manager = await self._ensure_admin_mcp_manager()
        async with self._global_lock:
            cfg = next((item for item in self._base_config.mcp.servers if item.name == server_name), None)
            if cfg is None:
                raise ValueError(f"Unknown MCP server: {server_name}")
            await manager.disconnect_server(server_name)
            for bot_id in self._bot_store.all_bot_ids():
                self._clear_bot_cache(bot_id)
        snapshot = await self.mcp_servers_snapshot()
        for server in snapshot:
            if server["name"] == server_name:
                return server
        raise ValueError(f"Unknown MCP server: {server_name}")

    def _skill_records(self) -> list[dict[str, Any]]:
        loader = SkillLoader([Path(p) for p in self.skills_dirs])
        records: list[dict[str, Any]] = []
        common_dirs = {path.expanduser().resolve() for path in loader.COMMON_SKILL_DIRS}

        for priority, skills_dir in enumerate(loader.skills_dirs):
            root = Path(skills_dir).expanduser().resolve()
            if not root.exists():
                continue
            for skill_dir in sorted(root.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                parsed = loader.parse_skill_md(skill_md)
                if not parsed:
                    continue

                source_type = self._classify_skill_source(root, common_dirs)
                resources = [
                    name
                    for name in self._ALLOWED_SKILL_RESOURCES
                    if (skill_dir / name).exists() and any((skill_dir / name).iterdir())
                ]
                records.append(
                    {
                        "name": str(parsed.get("name") or ""),
                        "description": str(parsed.get("description") or ""),
                        "folder_name": skill_dir.name,
                        "source_type": source_type,
                        "scope": source_type,
                        "source_dir": str(root),
                        "resolved_path": str(skill_dir.resolve()),
                        "resources": resources,
                        "writable": source_type in {"user", "project", "custom"},
                        "deletable": source_type == "user",
                        "builtin": source_type == "builtin",
                        "is_active": False,
                        "override_count": 0,
                        "overridden_by_source_type": None,
                        "overridden_by_path": None,
                        "_priority": priority,
                    }
                )

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            grouped.setdefault(item["name"], []).append(item)

        for items in grouped.values():
            items.sort(key=lambda item: int(item["_priority"]))
            active = items[0]
            active["is_active"] = True
            active["override_count"] = max(len(items) - 1, 0)
            for entry in items[1:]:
                entry["overridden_by_source_type"] = str(active["source_type"])
                entry["overridden_by_path"] = str(active["resolved_path"])

        for item in records:
            item.pop("_priority", None)

        return records

    def _classify_skill_source(self, root: Path, common_dirs: set[Path]) -> str:
        if root == self.project_skills_dir:
            return "project"
        if root == self.user_skills_dir:
            return "user"
        if root == SkillLoader.BUILTIN_SKILLS_DIR.resolve():
            return "builtin"
        if root in common_dirs:
            return "common"
        return "custom"

    def _skill_target_dir(self, scope: str) -> Path:
        if scope == "project":
            return self.project_skills_dir
        return self.user_skills_dir

    def _ensure_active_skill_dir(self, scope: str) -> None:
        target = str(self._skill_target_dir(scope))
        if target in self.skills_dirs:
            return
        if scope == "project":
            self.skills_dirs.insert(0, target)
            return
        project_dir = str(self.project_skills_dir)
        if project_dir in self.skills_dirs:
            self.skills_dirs.insert(1, target)
        else:
            self.skills_dirs.insert(0, target)

    def _find_skill_record(self, *, scope: str, folder_name: str) -> dict[str, Any] | None:
        for item in self._skill_records():
            if item["scope"] == scope and item["folder_name"] == folder_name:
                return item
        return None

    @classmethod
    def _normalize_skill_name(cls, skill_name: str) -> str:
        normalized = skill_name.strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
        normalized = normalized.strip("-")
        normalized = re.sub(r"-{2,}", "-", normalized)
        return normalized

    @staticmethod
    def _skill_title(skill_name: str) -> str:
        return " ".join(word.capitalize() for word in skill_name.split("-"))

    @classmethod
    def _skill_template(cls, skill_name: str, description: str | None) -> str:
        skill_title = cls._skill_title(skill_name)
        resolved_description = description or "[TODO: Describe what this skill does and when it should be used.]"
        return (
            f"---\n"
            f"name: {skill_name}\n"
            f"description: {resolved_description}\n"
            f"---\n\n"
            f"# {skill_title}\n\n"
            f"## Goal\n\n"
            f"[TODO: Explain the outcome this skill enables.]\n\n"
            f"## Workflow\n\n"
            f"1. [TODO: Describe the first important step.]\n"
            f"2. [TODO: Describe the second important step.]\n"
            f"3. [TODO: Describe how to verify the result.]\n\n"
            f"## Notes\n\n"
            f"- Keep this file concise.\n"
            f"- Move long docs into `references/` when needed.\n"
            f"- Add helper scripts only when deterministic execution matters.\n"
        )

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

        chat_model_id = data.get("chat_model_id")
        if isinstance(chat_model_id, str) and chat_model_id.strip():
            model_option = self._dashboard_store.model_option(chat_model_id.strip())
            if model_option is not None:
                provider = self._dashboard_store.provider_credentials(model_option["provider_id"])
                cfg.llm.base_url = str(provider.get("base_url") or cfg.llm.base_url)
                cfg.llm.api_key = provider.get("api_key") or cfg.llm.api_key
                cfg.llm.model = str(model_option.get("model_name") or cfg.llm.model)

        overrides = data.get("mcp_overrides")
        if isinstance(overrides, dict):
            for server in cfg.mcp.servers:
                value = overrides.get(server.name)
                if value is None:
                    continue
                server.enabled = bool(value)

        cfg.teams.enabled = bool(cfg.teams.enabled) and bool(data.get("teams_enabled", True))
        cfg.memory.active_namespace = self._optional_text(data.get("active_memory_namespace"))

        return cfg, data

    def _memory_manager_for(self, bot_id: str) -> MemoryManager:
        cfg, _data = self._build_bot_config(bot_id)
        return MemoryManager(cfg.memory, cfg.app_home, cfg.project_root)

    def list_memory_namespaces(self, bot_id: str) -> list[dict[str, Any]]:
        manager = self._memory_manager_for(bot_id)
        return [
            {
                "id": item.id,
                "slug": item.slug,
                "title": item.title,
                "description": item.description,
            }
            for item in manager.list_namespaces()
        ]

    async def create_memory_namespace(self, bot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        manager = self._memory_manager_for(bot_id)
        namespace = manager.create_namespace(
            str(patch.get("slug") or "").strip(),
            str(patch.get("title") or "").strip(),
            self._optional_text(patch.get("description")),
        )
        current = self.bot_config_snapshot(bot_id).get("active_memory_namespace")
        if not current:
            await self.update_bot_config(bot_id, {"active_memory_namespace": namespace.slug})
        return {
            "id": namespace.id,
            "slug": namespace.slug,
            "title": namespace.title,
            "description": namespace.description,
        }

    def create_memory_node(self, bot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        manager = self._memory_manager_for(bot_id)
        node = manager.create_memory(
            parent_uri=patch.get("parent_uri"),
            slug=self._optional_text(patch.get("slug")),
            title=str(patch.get("title") or "").strip(),
            kind=str(patch.get("kind") or "memory"),
            node_type=self._optional_text(patch.get("node_type")),
            content=str(patch.get("content") or ""),
            is_core=bool(patch.get("is_core", False)),
            priority=int(patch.get("priority", 0)),
        )
        return {
            "id": node.id,
            "uri": node.uri,
            "title": node.title,
            "kind": node.kind,
            "node_type": node.node_type,
            "is_core": node.is_core,
            "priority": node.priority,
        }

    def memory_search(self, bot_id: str, *, query: str, limit: int = 8) -> list[dict[str, Any]]:
        manager = self._memory_manager_for(bot_id)
        return [
            {
                "uri": item.uri,
                "title": item.title,
                "kind": item.kind,
                "node_type": item.node_type,
                "matched_by": item.matched_by,
                "snippet": item.snippet,
                "parent_uri": item.parent_uri,
            }
            for item in manager.search(query, limit=limit)
        ]

    def memory_view(self, bot_id: str, view_name: str) -> str:
        manager = self._memory_manager_for(bot_id)
        return manager.read(f"system://{view_name}")

    def memory_node(self, bot_id: str, *, uri: str) -> dict[str, Any]:
        manager = self._memory_manager_for(bot_id)
        try:
            return manager.node_detail(uri)
        except ValueError as exc:
            raise KeyError(uri) from exc

    def memory_tree(self, bot_id: str) -> dict[str, Any]:
        manager = self._memory_manager_for(bot_id)
        namespace = manager._active_namespace()
        rows = manager.repository.list_nodes(namespace)
        payloads = {
            node.id: {
                "uri": node.uri,
                "title": node.title,
                "kind": node.kind,
                "node_type": node.node_type,
                "children": [],
            }
            for node in rows
        }
        roots: list[dict[str, Any]] = []
        for node in rows:
            payload = payloads[node.id]
            if node.parent_id and node.parent_id in payloads:
                payloads[node.parent_id]["children"].append(payload)
            else:
                roots.append(payload)
        return {"nodes": roots}

    def memory_graph(self, bot_id: str) -> dict[str, Any]:
        tree = self.memory_tree(bot_id)["nodes"]
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []

        def walk(items: list[dict[str, Any]]) -> None:
            for item in items:
                nodes.append(
                    {
                        "uri": item["uri"],
                        "title": item["title"],
                        "kind": item["kind"],
                        "node_type": item["node_type"],
                    }
                )
                for child in item["children"]:
                    edges.append({"source": item["uri"], "target": child["uri"]})
                walk(item["children"])

        walk(tree)
        return {"nodes": nodes, "edges": edges}

    def _load_plugin_tools(self, plugin_paths: list[str]) -> list[Any]:
        tools: list[Any] = []
        for path in plugin_paths:
            try:
                tools.extend(load_tools_from_plugin(path))
            except Exception:
                continue
        return tools

    async def get_bot(self, bot_id: str, session_id: str) -> Minibot:
        self._assert_bot_chat_ready(bot_id)
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

            self._register_bot_subagents(owner_bot_id=bot_id, bot=bot)
            bot.agent.set_stream_enabled(bool(cfg.llm.stream_enabled))
            self._bots[key] = bot

        if key not in self._mcp_connected:
            try:
                await bot.connect_mcp()
            except Exception:
                pass
            self._mcp_connected.add(key)

        return bot

    async def delete_session(self, bot_id: str, session_id: str) -> bool:
        key = (bot_id, session_id)
        bot = self._bots.get(key)
        if bot is not None:
            bot.cancel()
        ok = self.sessions_for(bot_id).delete(session_id)
        async with self._global_lock:
            self._clear_session_cache(bot_id, session_id)
        return ok

    def _register_bot_subagents(self, *, owner_bot_id: str, bot: Minibot) -> None:
        data = self._bot_store.read_bot_json(owner_bot_id)
        attached_ids = list(data.get("attached_subagent_bot_ids") or [])
        added = False
        for subagent_bot_id in attached_ids:
            if subagent_bot_id == owner_bot_id:
                continue
            sub_data = self._bot_store.read_bot_json(subagent_bot_id)
            if not self._bot_enabled(sub_data) or not bool(sub_data.get("subagent_exposable", False)):
                continue
            label = self._effective_subagent_name(subagent_bot_id, sub_data)
            description = self._effective_subagent_description(subagent_bot_id, sub_data)
            bot.agent.agent_registry.register(
                AgentType(
                    name=f"bot_{subagent_bot_id}",
                    description=f"{label}: {description}",
                    tools="*",
                    prompt=f"Bot-backed subagent {label}",
                    skills_enabled=True,
                    metadata={"bot_id": subagent_bot_id, "label": label},
                    handler=self._make_bot_subagent_handler(subagent_bot_id),
                )
            )
            added = True
        if added:
            bot.agent.refresh_system_prompt()

    def _make_bot_subagent_handler(self, subagent_bot_id: str):
        async def _handler(description: str, prompt: str, _agent_type: AgentType) -> str:
            return await self._run_bot_subagent(
                subagent_bot_id=subagent_bot_id,
                description=description,
                prompt=prompt,
            )

        return _handler

    async def _run_bot_subagent(self, *, subagent_bot_id: str, description: str, prompt: str) -> str:
        cfg, data = self._build_bot_config(subagent_bot_id)
        if not self._bot_enabled(data):
            raise ValueError(f"Subagent bot '{subagent_bot_id}' is disabled")

        cfg.session.enabled = False
        plugin_tools = self._load_plugin_tools(list(data.get("tool_plugins") or []))
        soul = self._bot_store.read_soul(subagent_bot_id).strip()
        extra_system_prompt = f"# Soul\n\n{soul}" if soul else None
        skills_disabled = list(data.get("skills_disabled") or [])

        worker = Minibot(
            config=cfg,
            skills_dir=self.skills_dirs,
            tools=plugin_tools,
            system_prompt=extra_system_prompt,
            disabled_skills=skills_disabled,
            allow_subagent_delegation=False,
            allow_team_tools=False,
        )

        try:
            try:
                await worker.connect_mcp()
            except Exception:
                pass
            full_prompt = f"Task: {description}\n\n{prompt.strip()}".strip()
            result = await worker.chat(full_prompt)
            return result.assistant_text or "(subagent returned no text)"
        finally:
            await worker.agent.end_session()

    def _assert_bot_chat_ready(self, bot_id: str) -> None:
        state = self._bot_chat_state(bot_id)
        if not state["ready"]:
            raise ValueError(str(state["reason"] or "Bot is not available for chat"))

    def _bot_chat_state(self, bot_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = data if data is not None else self._bot_store.read_bot_json(bot_id)
        if not self._bot_enabled(data):
            return {"ready": False, "reason": "This bot is disabled."}

        chat_model_id = data.get("chat_model_id")
        if isinstance(chat_model_id, str) and chat_model_id.strip():
            model_option = self._dashboard_store.model_option(chat_model_id.strip())
            if model_option is None:
                return {"ready": False, "reason": "The selected chat model is disabled or unavailable."}
            return {"ready": True, "reason": None}

        cfg, _ = self._build_bot_config(bot_id)
        if not cfg.llm.model:
            return {"ready": False, "reason": "No chat model is configured for this bot."}
        return {"ready": True, "reason": None}

    async def _fetch_provider_model_names(self, *, base_url: str, api_key: str) -> list[str]:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        try:
            response = await client.models.list()
            data = getattr(response, "data", []) or []
            names = sorted(
                {
                    str(getattr(item, "id", "")).strip()
                    for item in data
                    if str(getattr(item, "id", "")).strip()
                }
            )
            return names
        finally:
            await client.close()

    def _normalize_attached_subagents(self, *, owner_bot_id: str, raw: Any) -> list[str]:
        items = raw if isinstance(raw, list) else []
        seen: set[str] = set()
        out: list[str] = []
        existing = set(self._bot_store.all_bot_ids())
        for item in items:
            candidate = str(item).strip()
            if not candidate or candidate in seen:
                continue
            if candidate == owner_bot_id:
                raise ValueError("A bot cannot attach itself as a subagent")
            if candidate not in existing:
                raise ValueError(f"Unknown subagent bot: {candidate}")
            data = self._bot_store.read_bot_json(candidate)
            if not self._bot_enabled(data):
                raise ValueError(f"Subagent bot '{candidate}' is disabled")
            if not bool(data.get("subagent_exposable", False)):
                raise ValueError(f"Bot '{candidate}' is not available as a subagent")
            seen.add(candidate)
            out.append(candidate)
        return out

    def _bot_ids_using_model(self, model_id: str) -> list[str]:
        out: list[str] = []
        for bot_id in self._bot_store.all_bot_ids():
            data = self._bot_store.read_bot_json(bot_id)
            if data.get("chat_model_id") == model_id:
                out.append(bot_id)
        return out

    def _bot_ids_using_provider(self, provider_id: str) -> list[str]:
        model_ids = {
            item["model_id"]
            for item in self._dashboard_store.list_models()
            if item.get("provider_id") == provider_id
        }
        if not model_ids:
            return []
        out: list[str] = []
        for bot_id in self._bot_store.all_bot_ids():
            data = self._bot_store.read_bot_json(bot_id)
            if data.get("chat_model_id") in model_ids:
                out.append(bot_id)
        return out

    def _bots_referencing(self, target_bot_id: str) -> list[str]:
        out: list[str] = []
        for bot_id in self._bot_store.all_bot_ids():
            data = self._bot_store.read_bot_json(bot_id)
            attached = data.get("attached_subagent_bot_ids")
            if isinstance(attached, list) and target_bot_id in attached:
                out.append(bot_id)
        return out

    def _effective_subagent_name(self, bot_id: str, data: dict[str, Any]) -> str:
        raw = self._optional_text(data.get("subagent_name"))
        if raw:
            return raw
        return self._display_bot_name(bot_id, data)

    def _effective_subagent_description(self, bot_id: str, data: dict[str, Any]) -> str:
        raw = self._optional_text(data.get("subagent_description"))
        if raw:
            return raw
        soul = self._soul_summary(self._bot_store.read_soul(bot_id))
        if soul:
            return soul
        return f"{self._effective_subagent_name(bot_id, data)} subagent"

    @staticmethod
    def _soul_summary(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        summary = lines[0]
        if len(summary) > 120:
            return summary[:117] + "..."
        return summary

    def _clear_bot_cache(self, bot_id: str) -> None:
        keys = [k for k in self._bots.keys() if k[0] == bot_id]
        for key in keys:
            self._bots.pop(key, None)
            self._locks.pop(key, None)
            self._mcp_connected.discard(key)

    def _clear_session_cache(self, bot_id: str, session_id: str, *, preserve_lock: bool = False) -> None:
        key = (bot_id, session_id)
        bot = self._bots.pop(key, None)
        if bot is not None:
            bot.cancel()
        if not preserve_lock:
            self._locks.pop(key, None)
        self._mcp_connected.discard(key)

    @classmethod
    def _serialize_messages(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for raw in messages:
            item = dict(raw)
            tool_calls = cls._normalize_tool_calls(item.get("tool_calls"))
            if "tool_calls" in item:
                if tool_calls:
                    item["tool_calls"] = tool_calls
                else:
                    item.pop("tool_calls", None)
            for key in ("usage", "context_usage"):
                usage = cls._normalize_usage(item.get(key))
                if usage is None:
                    item.pop(key, None)
                else:
                    item[key] = usage
            serialized.append(item)
        return serialized

    @staticmethod
    def _normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []

        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(value, start=1):
            if not isinstance(raw, dict):
                continue
            tool_id = str(raw.get("id") or f"tool-call-{index}")
            if isinstance(raw.get("function"), dict):
                function = dict(raw["function"])
                normalized.append(
                    {
                        "id": tool_id,
                        "type": str(raw.get("type") or "function"),
                        "function": {
                            "name": str(function.get("name") or ""),
                            "arguments": str(function.get("arguments") or ""),
                        },
                    }
                )
                continue

            normalized.append(
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": str(raw.get("name") or ""),
                        "arguments": str(raw.get("arguments") or ""),
                    },
                }
            )
        return normalized

    @classmethod
    def _assistant_turn_bounds(cls, messages: list[dict[str, Any]], message_id: str) -> tuple[int, int, int]:
        assistant_index = -1
        for index, message in enumerate(messages):
            if message.get("message_id") == message_id:
                assistant_index = index
                break
        if assistant_index < 0:
            raise ValueError("Message not found")

        assistant_message = messages[assistant_index]
        if assistant_message.get("role") != "assistant":
            raise ValueError("Only assistant messages support this action")

        parent_user_message_id = assistant_message.get("parent_user_message_id")
        user_index = -1
        if isinstance(parent_user_message_id, str) and parent_user_message_id.strip():
            for index in range(assistant_index, -1, -1):
                candidate = messages[index]
                if candidate.get("role") != "user":
                    continue
                if candidate.get("message_id") == parent_user_message_id:
                    user_index = index
                    break
        if user_index < 0:
            for index in range(assistant_index, -1, -1):
                if messages[index].get("role") == "user":
                    user_index = index
                    break
        if user_index < 0:
            raise ValueError("Could not find the user message for this assistant turn")

        turn_end = len(messages)
        for index in range(user_index + 1, len(messages)):
            if messages[index].get("role") == "user":
                turn_end = index
                break
        if assistant_index >= turn_end:
            raise ValueError("Message turn is invalid")
        return user_index, turn_end, user_index

    @staticmethod
    def _normalize_usage(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        normalized: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            raw = value.get(key)
            if raw is None:
                continue
            try:
                normalized[key] = int(raw)
            except (TypeError, ValueError):
                continue
        return normalized or None

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None if value is None else str(value).strip() or None
        text = value.strip()
        return text or None

    @staticmethod
    def _bot_enabled(data: dict[str, Any]) -> bool:
        return bool(data.get("enabled", True))

    def bot_exists(self, bot_id: str) -> bool:
        return bot_id in self._bot_store.all_bot_ids()

    def set_platform_runtime_state(
        self,
        platform_id: str,
        *,
        connected: Any = _STATE_UNSET,
        last_error: Any = _STATE_UNSET,
        last_event_at: Any = _STATE_UNSET,
    ) -> None:
        state = self._platform_runtime_state_for(platform_id)
        if connected is not _STATE_UNSET:
            state["connected"] = bool(connected)
        if last_error is not _STATE_UNSET:
            state["last_error"] = last_error
        if last_event_at is not _STATE_UNSET:
            state["last_event_at"] = last_event_at

    def _platform_runtime_state_for(self, platform_id: str) -> dict[str, Any]:
        state = self._platform_runtime_state.get(platform_id)
        if state is None:
            state = {"connected": False, "last_error": None, "last_event_at": None}
            self._platform_runtime_state[platform_id] = state
        return state

    def _build_platform_runtime(self, platform: dict[str, Any]) -> Any:
        kind = str(platform.get("kind") or "").strip().lower()
        if kind == "feishu":
            return FeishuPlatformRuntime(manager=self, platform=platform)
        raise NotImplementedError(f"{platform_kind_label(kind)} runtime is not implemented yet.")

    async def _sync_platform_runtime(self, platform_id: str) -> None:
        await self._stop_platform_runtime(platform_id, clear_state=False)

        async with self._global_lock:
            if not self._platforms_started:
                return
            try:
                platform = self._platform_store.platform_for_update(platform_id)
            except KeyError:
                self._platform_runtime_state.pop(platform_id, None)
                return

        if not bool(platform.get("enabled", True)):
            self.set_platform_runtime_state(platform_id, connected=False)
            return

        try:
            runtime = self._build_platform_runtime(platform)
        except Exception as exc:
            self.set_platform_runtime_state(platform_id, connected=False, last_error=str(exc))
            return

        async with self._global_lock:
            if not self._platforms_started:
                return
            self._platform_runtimes[platform_id] = runtime

        await runtime.start()

    async def _stop_platform_runtime(self, platform_id: str, *, clear_state: bool) -> None:
        async with self._global_lock:
            runtime = self._platform_runtimes.pop(platform_id, None)
        if runtime is not None:
            await runtime.stop()
        if clear_state:
            self._platform_runtime_state.pop(platform_id, None)
        else:
            self.set_platform_runtime_state(platform_id, connected=False)

    @staticmethod
    def _mask_api_key(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 6:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
