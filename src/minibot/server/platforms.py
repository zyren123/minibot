"""Persistence for external platform connections."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bots import DEFAULT_BOT_ID


SUPPORTED_PLATFORM_KINDS = {"feishu", "telegram", "whatsapp"}
PLATFORM_KIND_LABELS = {
    "feishu": "Feishu",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
}
DEFAULT_PLATFORM_MODE = "websocket"
DEFAULT_PLATFORM_SCOPE = "private"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_platform_id() -> str:
    return f"plat_{uuid.uuid4().hex[:10]}"


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def platform_kind_label(kind: str) -> str:
    return PLATFORM_KIND_LABELS.get(str(kind or "").strip().lower(), str(kind or "").strip() or "Platform")


class PlatformStore:
    """JSON-backed registry for external platform connections."""

    def __init__(self, *, app_home: Path) -> None:
        self.app_home = app_home.resolve()
        self.path = (self.app_home / "platforms.json").resolve()

    def list_platforms(self) -> list[dict[str, Any]]:
        data = self._read()
        return [self._platform_public(item) for item in data.get("platforms", [])]

    def platform_for_update(self, platform_id: str) -> dict[str, Any]:
        data = self._read()
        for item in data.get("platforms", []):
            if item.get("platform_id") == platform_id:
                return dict(item)
        raise KeyError(platform_id)

    def create_platform(
        self,
        *,
        name: str,
        kind: str,
        bound_bot_id: str,
        enabled: bool = True,
        app_id: str = "",
        app_secret: str = "",
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in SUPPORTED_PLATFORM_KINDS:
            raise ValueError(f"Unsupported platform kind: {kind}")

        now = _utc_now_iso()
        data = self._read()
        item = {
            "platform_id": _new_platform_id(),
            "name": name.strip(),
            "kind": normalized_kind,
            "enabled": bool(enabled),
            "bound_bot_id": str(bound_bot_id or DEFAULT_BOT_ID).strip() or DEFAULT_BOT_ID,
            "mode": DEFAULT_PLATFORM_MODE,
            "scope": DEFAULT_PLATFORM_SCOPE,
            "app_id": app_id.strip(),
            "app_secret": app_secret.strip(),
            "created_at": now,
            "updated_at": now,
        }
        data.setdefault("platforms", []).append(item)
        self._write(data)
        return self._platform_public(item)

    def update_platform(self, platform_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        for item in data.get("platforms", []):
            if item.get("platform_id") != platform_id:
                continue

            if "name" in patch and isinstance(patch.get("name"), str) and patch.get("name").strip():
                item["name"] = patch.get("name").strip()
            if "enabled" in patch and patch.get("enabled") is not None:
                item["enabled"] = bool(patch.get("enabled"))
            if "bound_bot_id" in patch and isinstance(patch.get("bound_bot_id"), str):
                item["bound_bot_id"] = patch.get("bound_bot_id").strip() or DEFAULT_BOT_ID
            if "app_id" in patch and isinstance(patch.get("app_id"), str) and patch.get("app_id").strip():
                item["app_id"] = patch.get("app_id").strip()
            if "app_secret" in patch:
                raw = patch.get("app_secret")
                if isinstance(raw, str) and raw.strip():
                    item["app_secret"] = raw.strip()
            item["updated_at"] = _utc_now_iso()
            self._write(data)
            return self._platform_public(item)

        raise KeyError(platform_id)

    def delete_platform(self, platform_id: str) -> bool:
        data = self._read()
        kept = [item for item in data.get("platforms", []) if item.get("platform_id") != platform_id]
        if len(kept) == len(data.get("platforms", [])):
            return False
        data["platforms"] = kept
        self._write(data)
        return True

    def platform_ids_bound_to_bot(self, bot_id: str) -> list[str]:
        out: list[str] = []
        for item in self._read().get("platforms", []):
            if item.get("bound_bot_id") == bot_id:
                out.append(str(item.get("platform_id")))
        return out

    def rebind_bot(self, *, source_bot_id: str, target_bot_id: str) -> list[str]:
        data = self._read()
        updated_ids: list[str] = []
        for item in data.get("platforms", []):
            if item.get("bound_bot_id") != source_bot_id:
                continue
            item["bound_bot_id"] = target_bot_id
            item["updated_at"] = _utc_now_iso()
            updated_ids.append(str(item.get("platform_id")))
        if updated_ids:
            self._write(data)
        return updated_ids

    def all_platform_ids(self) -> list[str]:
        return [str(item.get("platform_id")) for item in self._read().get("platforms", [])]

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"platforms": []}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"platforms": []}
        if not isinstance(parsed, dict):
            return {"platforms": []}
        parsed.setdefault("platforms", [])
        return parsed

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _platform_public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform_id": item.get("platform_id"),
            "name": item.get("name") or "",
            "kind": item.get("kind") or "feishu",
            "enabled": bool(item.get("enabled", True)),
            "bound_bot_id": item.get("bound_bot_id") or DEFAULT_BOT_ID,
            "mode": item.get("mode") or DEFAULT_PLATFORM_MODE,
            "scope": item.get("scope") or DEFAULT_PLATFORM_SCOPE,
            "app_id": item.get("app_id") or "",
            "app_secret_masked": _mask_secret(item.get("app_secret")),
            "created_at": item.get("created_at") or "",
            "updated_at": item.get("updated_at") or "",
        }


class ConversationMapStore:
    """Persistence for external-chat to local-session mappings."""

    def __init__(self, *, app_home: Path, platform_id: str) -> None:
        self.app_home = app_home.resolve()
        self.platform_id = platform_id
        self.dir = (self.app_home / "platform_connections" / platform_id).resolve()
        self.path = (self.dir / "conversation_map.json").resolve()

    def get(self, external_chat_id: str) -> dict[str, Any] | None:
        data = self._read()
        entry = data.get("conversations", {}).get(external_chat_id)
        if isinstance(entry, dict):
            return dict(entry)
        return None

    def set_mapping(
        self,
        *,
        external_chat_id: str,
        session_id: str,
        last_message_id: str | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        entry = {
            "external_chat_id": external_chat_id,
            "session_id": session_id,
            "last_message_id": last_message_id,
            "updated_at": _utc_now_iso(),
        }
        data.setdefault("conversations", {})[external_chat_id] = entry
        self._write(data)
        return dict(entry)

    def clear(self) -> None:
        self._write({"conversations": {}})

    def delete_storage(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir)

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"conversations": {}}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"conversations": {}}
        if not isinstance(parsed, dict):
            return {"conversations": {}}
        conversations = parsed.get("conversations")
        if not isinstance(conversations, dict):
            parsed["conversations"] = {}
        return parsed

    def _write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
