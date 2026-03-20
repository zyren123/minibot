"""Bot persistence for Minibot server (WebUI)."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BOT_ID = "default"

_BOT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _default_bot_display_name(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip() and raw.strip() != "Default":
        return raw.strip()
    return "Minibot"


def _mask_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def _generate_bot_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class BotMeta:
    bot_id: str
    name: str
    is_default: bool


class BotStore:
    """File-based bot store.

    Layout:
      - Default bot home: <app_home>/
      - Custom bots root: <app_home>/bots/<bot_id>/
        - bot.json
        - soul.md
        - sessions/
        - memory/
        - state/
    """

    def __init__(self, *, app_home: Path) -> None:
        self.app_home = app_home.resolve()
        self.bots_root = (self.app_home / "bots").resolve()
        self.bots_root.mkdir(parents=True, exist_ok=True)

    def bot_home(self, bot_id: str) -> Path:
        if bot_id == DEFAULT_BOT_ID:
            return self.app_home
        self._validate_bot_id(bot_id)
        return (self.bots_root / bot_id).resolve()

    def list_bots(self) -> list[BotMeta]:
        bots: list[BotMeta] = []

        default_name = _default_bot_display_name(self._read_bot_json(DEFAULT_BOT_ID).get("name"))
        bots.append(BotMeta(bot_id=DEFAULT_BOT_ID, name=str(default_name), is_default=True))

        for path in sorted(self.bots_root.iterdir()):
            if not path.is_dir():
                continue
            bot_id = path.name
            if not _BOT_ID_RE.match(bot_id):
                continue
            name = self._read_bot_json(bot_id).get("name") or bot_id
            bots.append(BotMeta(bot_id=bot_id, name=str(name), is_default=False))

        return bots

    def all_bot_ids(self) -> list[str]:
        return [meta.bot_id for meta in self.list_bots()]

    def create_bot(self, *, name: str | None = None) -> BotMeta:
        bot_id = _generate_bot_id()
        while (self.bots_root / bot_id).exists():
            bot_id = _generate_bot_id()

        home = self.bot_home(bot_id)
        home.mkdir(parents=True, exist_ok=False)

        data: dict[str, Any] = {}
        if name and name.strip():
            data["name"] = name.strip()

        self._write_bot_json(bot_id, data)
        return BotMeta(bot_id=bot_id, name=data.get("name") or bot_id, is_default=False)

    def delete_bot(self, bot_id: str) -> bool:
        if bot_id == DEFAULT_BOT_ID:
            raise ValueError("Cannot delete default bot")
        home = self.bot_home(bot_id)
        if not home.exists():
            return False

        # Safety: only allow deletion inside bots_root.
        if not home.is_relative_to(self.bots_root):
            raise ValueError("Refusing to delete bot outside bots root")

        shutil.rmtree(home)
        self.remove_subagent_references(bot_id)
        return True

    def read_soul(self, bot_id: str) -> str:
        path = self.bot_home(bot_id) / "soul.md"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def write_soul(self, bot_id: str, text: str) -> None:
        home = self.bot_home(bot_id)
        home.mkdir(parents=True, exist_ok=True)
        (home / "soul.md").write_text((text or "").rstrip() + "\n", encoding="utf-8")

    def read_bot_json(self, bot_id: str) -> dict[str, Any]:
        return dict(self._read_bot_json(bot_id))

    def patch_bot_json(self, bot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read_bot_json(bot_id)
        for key, value in patch.items():
            if value is None:
                data.pop(key, None)
                continue
            data[key] = value
        self._write_bot_json(bot_id, data)
        return dict(data)

    def remove_subagent_references(self, subagent_bot_id: str) -> None:
        for bot_id in self.all_bot_ids():
            data = self._read_bot_json(bot_id)
            attached = data.get("attached_subagent_bot_ids")
            if not isinstance(attached, list) or subagent_bot_id not in attached:
                continue
            data["attached_subagent_bot_ids"] = [item for item in attached if item != subagent_bot_id]
            self._write_bot_json(bot_id, data)

    def remove_skill_references(self, skill_name: str) -> None:
        for bot_id in self.all_bot_ids():
            data = self._read_bot_json(bot_id)
            disabled = data.get("skills_disabled")
            if not isinstance(disabled, list) or skill_name not in disabled:
                continue
            data["skills_disabled"] = [item for item in disabled if item != skill_name]
            self._write_bot_json(bot_id, data)

    def _read_bot_json(self, bot_id: str) -> dict[str, Any]:
        path = self.bot_home(bot_id) / "bot.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _write_bot_json(self, bot_id: str, data: dict[str, Any]) -> None:
        home = self.bot_home(bot_id)
        home.mkdir(parents=True, exist_ok=True)
        path = home / "bot.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def api_key_masked(data: dict[str, Any]) -> str | None:
        llm = data.get("llm")
        if isinstance(llm, dict):
            return _mask_api_key(llm.get("api_key"))
        return None

    @staticmethod
    def _validate_bot_id(bot_id: str) -> None:
        if not _BOT_ID_RE.match(bot_id):
            raise ValueError("Invalid bot_id")
