"""In-process agent/session manager for the web server."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import re
import shutil
from typing import Any

from openai import AsyncOpenAI

from ..config import Config, load_config
from ..sdk import Minibot
from ..session.manager import SessionManager
from ..skills.loader import SkillLoader
from ..subagents.registry import AgentType

from .bots import BotStore, DEFAULT_BOT_ID
from .dashboard_store import DashboardStore
from .plugins import load_tools_from_plugin


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
        self._bot_store = BotStore(app_home=Path(self._base_config.app_home))
        self._dashboard_store = DashboardStore(app_home=Path(self._base_config.app_home))
        self.user_skills_dir = Path(self._base_config.skills_dir).resolve()
        self.project_skills_dir = (Path(self._base_config.project_root) / "skills").resolve()

        self._bots: dict[tuple[str, str], Minibot] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._mcp_connected: set[tuple[str, str]] = set()
        self._sessions: dict[str, SessionManager] = {}
        self._global_lock = asyncio.Lock()

        default_dirs = [str(self.project_skills_dir), str(self.user_skills_dir)]
        self.skills_dirs = list(dict.fromkeys(default_dirs))

    def config_snapshot(self) -> dict[str, Any]:
        bot_cfg = self.bot_config_snapshot(DEFAULT_BOT_ID)
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

    def dashboard_snapshot(self) -> dict[str, Any]:
        snapshot = self._dashboard_store.snapshot()
        return {
            "providers": snapshot["providers"],
            "models": snapshot["models"],
            "bots": self.list_bots(),
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
        async with self._global_lock:
            referencing = set(self._bots_referencing(bot_id))
            deleted = self._bot_store.delete_bot(bot_id)
            if deleted:
                self._clear_bot_cache(bot_id)
                for owner_bot_id in referencing:
                    self._clear_bot_cache(owner_bot_id)
                self._sessions.pop(bot_id, None)
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
            "api_key_masked": self._mask_api_key(cfg.llm.api_key),
            "tool_plugins": list(data.get("tool_plugins") or []),
            "skills_disabled": list(data.get("skills_disabled") or []),
            "mcp_overrides": dict(data.get("mcp_overrides") or {}),
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

    def update_provider(self, provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        updated = self._dashboard_store.update_provider(provider_id, patch)
        for bot_id in self._bot_ids_using_provider(provider_id):
            self._clear_bot_cache(bot_id)
        return updated

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

        return cfg, data

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
            for key in ("usage", "context_usage"):
                usage = cls._normalize_usage(item.get(key))
                if usage is None:
                    item.pop(key, None)
                else:
                    item[key] = usage
            serialized.append(item)
        return serialized

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

    @staticmethod
    def _mask_api_key(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 6:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
