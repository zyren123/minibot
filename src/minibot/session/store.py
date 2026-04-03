"""JSONL-based session state persistence."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .messages import normalize_session_message

PREVIEW_MAX_LEN = 60


@dataclass(frozen=True)
class SessionMeta:
    """Metadata for a persisted session."""

    session_id: str
    path: Path
    created_at: datetime
    modified_at: datetime
    message_count: int
    preview: str  # first user message, truncated


class SessionStore:
    """Low-level JSONL reader/writer for session history.

    Storage layout: ``<sessions_dir>/yyyy/mm/dd/<session_id>.jsonl``
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._root = sessions_dir.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _date_subdir(dt: datetime) -> str:
        return f"{dt:%Y/%m/%d}"

    def _resolve_path(self, session_id: str, date: datetime) -> Path:
        return self._root / self._date_subdir(date) / f"{session_id}.jsonl"

    def _find_path(self, session_id: str) -> Path | None:
        """Find an existing session file by scanning date subdirs."""
        for path in self._root.rglob(f"{session_id}.jsonl"):
            return path
        return None

    def runtime_state_path(self, session_id: str) -> Path | None:
        """Return the runtime sidecar path for an existing session file."""
        path = self._find_path(session_id)
        if path is None:
            return None
        return path.with_suffix(".runtime.json")

    def create(self, session_id: str, date: datetime | None = None) -> Path:
        """Create an empty JSONL file and return its path."""
        dt = date or datetime.now()
        path = self._resolve_path(session_id, dt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        return path

    def append(self, session_id: str, message: dict) -> None:
        """Append a single message to the session JSONL file."""
        path = self._find_path(session_id)
        if path is None:
            path = self.create(session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def overwrite(self, session_id: str, messages: list[dict]) -> None:
        """Rewrite the session JSONL file with the provided messages."""
        path = self._find_path(session_id)
        if path is None:
            path = self.create(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for message in messages:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def update_message(
        self,
        session_id: str,
        message_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Update the first persisted message with a matching message_id."""
        path = self._find_path(session_id)
        if path is None:
            return None

        messages = self._read_jsonl(path)
        for index, message in enumerate(messages):
            if message.get("message_id") != message_id:
                continue

            current = dict(message)
            updated = updater(current)
            messages[index] = dict(updated) if updated is not None else current
            self.overwrite(session_id, messages)
            return messages[index]

        return None

    def save_runtime_state(self, session_id: str, payload: dict[str, Any]) -> None:
        """Persist runtime sidecar state as a small JSON file."""
        path = self.runtime_state_path(session_id)
        if path is None:
            session_path = self.create(session_id)
            path = session_path.with_suffix(".runtime.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, payload)

    def load_runtime_state(self, session_id: str) -> dict[str, Any] | None:
        """Load runtime sidecar state if present."""
        path = self.runtime_state_path(session_id)
        if path is None or not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete_runtime_state(self, session_id: str) -> bool:
        """Delete the runtime sidecar if present."""
        path = self.runtime_state_path(session_id)
        if path is None or not path.exists():
            return False
        path.unlink()
        return True

    def load(self, session_id: str) -> list[dict]:
        """Load conversation history from JSONL, starting from the last compaction point."""
        path = self._find_path(session_id)
        if path is None:
            return []
        
        messages = self._read_jsonl(path)
        
        # Find the last compaction message if any
        compaction_index = -1
        for i, msg in enumerate(messages):
            if msg.get("is_compaction"):
                compaction_index = i
                
        if compaction_index >= 0:
            return [normalize_session_message(message) for message in messages[compaction_index:]]
            
        return [normalize_session_message(message) for message in messages]

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        messages: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            messages.append(json.loads(line))
        return messages

    def delete(self, session_id: str) -> bool:
        """Delete a session file. Returns True if found and deleted."""
        path = self._find_path(session_id)
        if path is None:
            return False
        runtime_path = path.with_suffix(".runtime.json")
        path.unlink()
        if runtime_path.exists():
            runtime_path.unlink()
        # Clean up empty parent date dirs
        for parent in [path.parent, path.parent.parent, path.parent.parent.parent]:
            if parent == self._root:
                break
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        return True

    def list_sessions(self) -> list[SessionMeta]:
        """List all sessions, sorted by modification time descending."""
        results: list[SessionMeta] = []
        for path in self._root.rglob("*.jsonl"):
            meta = self._build_meta(path)
            if meta is not None:
                results.append(meta)
        results.sort(key=lambda m: m.modified_at, reverse=True)
        return results

    def _build_meta(self, path: Path) -> SessionMeta | None:
        session_id = path.stem
        try:
            stat = path.stat()
        except OSError:
            return None

        # macOS provides st_birthtime; Linux often does not. Fall back to st_ctime.
        created_ts = getattr(stat, "st_birthtime", None)
        if created_ts is None:
            created_ts = stat.st_ctime
        created_at = datetime.fromtimestamp(created_ts)
        modified_at = datetime.fromtimestamp(stat.st_mtime)

        messages = self._read_jsonl(path)
        preview = ""
        for msg in messages:
            if msg.get("role") == "user":
                raw = msg.get("content", "")
                preview = (raw[:PREVIEW_MAX_LEN - 3] + "...") if len(raw) > PREVIEW_MAX_LEN else raw
                break

        return SessionMeta(
            session_id=session_id,
            path=path,
            created_at=created_at,
            modified_at=modified_at,
            message_count=len(messages),
            preview=preview,
        )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        """Write JSON to disk atomically within the target directory."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False)
            tmp.flush()
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
