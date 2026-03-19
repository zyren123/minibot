"""Persistence for global WebUI dashboard state."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


@dataclass(frozen=True)
class ProviderRecord:
    provider_id: str
    name: str
    base_url: str
    kind: str
    enabled: bool
    api_key_masked: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    provider_id: str
    model_name: str
    label: str
    enabled: bool
    added_via: str
    created_at: str
    updated_at: str


class DashboardStore:
    """JSON-backed persistence for provider/model registry."""

    def __init__(self, *, app_home: Path) -> None:
        self.app_home = app_home.resolve()
        self.path = (self.app_home / "dashboard.json").resolve()

    def snapshot(self) -> dict[str, Any]:
        data = self._read()
        return {
            "providers": [self._provider_public(item) for item in data.get("providers", [])],
            "models": [self._model_public(item) for item in data.get("models", [])],
        }

    def list_providers(self) -> list[dict[str, Any]]:
        return list(self.snapshot()["providers"])

    def list_models(self) -> list[dict[str, Any]]:
        return list(self.snapshot()["models"])

    def provider_for_update(self, provider_id: str) -> dict[str, Any]:
        data = self._read()
        for item in data.get("providers", []):
            if item.get("provider_id") == provider_id:
                return dict(item)
        raise KeyError(provider_id)

    def model_for_update(self, model_id: str) -> dict[str, Any]:
        data = self._read()
        for item in data.get("models", []):
            if item.get("model_id") == model_id:
                return dict(item)
        raise KeyError(model_id)

    def create_provider(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        enabled: bool = True,
        kind: str = "openai_compatible",
    ) -> dict[str, Any]:
        data = self._read()
        now = _utc_now_iso()
        provider = {
            "provider_id": _new_id("prov"),
            "name": name.strip(),
            "base_url": base_url.strip().rstrip("/"),
            "api_key": api_key.strip() if isinstance(api_key, str) and api_key.strip() else None,
            "kind": kind,
            "enabled": bool(enabled),
            "created_at": now,
            "updated_at": now,
        }
        data.setdefault("providers", []).append(provider)
        self._write(data)
        return self._provider_public(provider)

    def update_provider(self, provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        providers = data.setdefault("providers", [])
        for item in providers:
            if item.get("provider_id") != provider_id:
                continue
            if "name" in patch and isinstance(patch.get("name"), str) and patch.get("name").strip():
                item["name"] = patch.get("name").strip()
            if "base_url" in patch and isinstance(patch.get("base_url"), str) and patch.get("base_url").strip():
                item["base_url"] = patch.get("base_url").strip().rstrip("/")
            if "enabled" in patch and patch.get("enabled") is not None:
                item["enabled"] = bool(patch.get("enabled"))
            if "api_key" in patch:
                raw = patch.get("api_key")
                item["api_key"] = raw.strip() if isinstance(raw, str) and raw.strip() else None
            item["updated_at"] = _utc_now_iso()
            self._write(data)
            return self._provider_public(item)
        raise KeyError(provider_id)

    def create_models(
        self,
        *,
        provider_id: str,
        model_names: list[str],
        added_via: str,
    ) -> list[dict[str, Any]]:
        if not model_names:
            return []
        data = self._read()
        providers = {item.get("provider_id") for item in data.get("providers", [])}
        if provider_id not in providers:
            raise KeyError(provider_id)

        models = data.setdefault("models", [])
        existing_names = {
            (item.get("provider_id"), item.get("model_name"))
            for item in models
            if isinstance(item, dict)
        }
        added: list[dict[str, Any]] = []
        now = _utc_now_iso()
        for raw_name in model_names:
            name = str(raw_name).strip()
            if not name or (provider_id, name) in existing_names:
                continue
            item = {
                "model_id": _new_id("model"),
                "provider_id": provider_id,
                "model_name": name,
                "label": name,
                "enabled": True,
                "added_via": added_via,
                "created_at": now,
                "updated_at": now,
            }
            models.append(item)
            existing_names.add((provider_id, name))
            added.append(self._model_public(item))
        if added:
            self._write(data)
        return added

    def update_model(self, model_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        models = data.setdefault("models", [])
        for item in models:
            if item.get("model_id") != model_id:
                continue
            if "label" in patch and isinstance(patch.get("label"), str) and patch.get("label").strip():
                item["label"] = patch.get("label").strip()
            if "enabled" in patch and patch.get("enabled") is not None:
                item["enabled"] = bool(patch.get("enabled"))
            item["updated_at"] = _utc_now_iso()
            self._write(data)
            return self._model_public(item)
        raise KeyError(model_id)

    def delete_models(self, *, provider_id: str, model_ids: list[str]) -> list[str]:
        data = self._read()
        providers = {item.get("provider_id") for item in data.get("providers", [])}
        if provider_id not in providers:
            raise KeyError(provider_id)

        targets = {str(item).strip() for item in model_ids if str(item).strip()}
        if not targets:
            return []

        deleted_ids: list[str] = []
        kept_models: list[dict[str, Any]] = []
        for item in data.get("models", []):
            if item.get("provider_id") != provider_id or item.get("model_id") not in targets:
                kept_models.append(item)
                continue
            deleted_ids.append(str(item.get("model_id")))

        if not deleted_ids:
            return []

        data["models"] = kept_models
        self._write(data)
        return deleted_ids

    def delete_provider_models(self, provider_id: str) -> None:
        data = self._read()
        models = [item for item in data.get("models", []) if item.get("provider_id") != provider_id]
        if len(models) == len(data.get("models", [])):
            return
        data["models"] = models
        self._write(data)

    def active_model_options(self) -> list[dict[str, Any]]:
        snapshot = self.snapshot()
        providers = {item["provider_id"]: item for item in snapshot["providers"]}
        options: list[dict[str, Any]] = []
        for item in snapshot["models"]:
            provider = providers.get(item["provider_id"])
            if provider is None or not provider.get("enabled") or not item.get("enabled"):
                continue
            options.append(
                {
                    "model_id": item["model_id"],
                    "provider_id": provider["provider_id"],
                    "provider_name": provider["name"],
                    "model_name": item["model_name"],
                    "label": item["label"],
                    "base_url": provider["base_url"],
                }
            )
        options.sort(key=lambda d: (str(d["provider_name"]).lower(), str(d["label"]).lower()))
        return options

    def model_option(self, model_id: str) -> dict[str, Any] | None:
        for item in self.active_model_options():
            if item["model_id"] == model_id:
                return item
        return None

    def provider_credentials(self, provider_id: str) -> dict[str, Any]:
        item = self.provider_for_update(provider_id)
        return {
            "provider_id": provider_id,
            "name": item.get("name"),
            "base_url": str(item.get("base_url") or ""),
            "api_key": item.get("api_key"),
            "enabled": bool(item.get("enabled")),
            "kind": item.get("kind") or "openai_compatible",
        }

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"providers": [], "models": []}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"providers": [], "models": []}
        if not isinstance(parsed, dict):
            return {"providers": [], "models": []}
        parsed.setdefault("providers", [])
        parsed.setdefault("models", [])
        return parsed

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _provider_public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider_id": item.get("provider_id"),
            "name": item.get("name") or "",
            "base_url": item.get("base_url") or "",
            "kind": item.get("kind") or "openai_compatible",
            "enabled": bool(item.get("enabled", True)),
            "api_key_masked": _mask_secret(item.get("api_key")),
            "created_at": item.get("created_at") or "",
            "updated_at": item.get("updated_at") or "",
        }

    @staticmethod
    def _model_public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_id": item.get("model_id"),
            "provider_id": item.get("provider_id"),
            "model_name": item.get("model_name") or "",
            "label": item.get("label") or item.get("model_name") or "",
            "enabled": bool(item.get("enabled", True)),
            "added_via": item.get("added_via") or "manual",
            "created_at": item.get("created_at") or "",
            "updated_at": item.get("updated_at") or "",
        }
