"""Session lifecycle management."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from .store import SessionStore, SessionMeta

SESSION_ID_LEN = 8


class SessionManager:
    """High-level session lifecycle: create, load, save, list, delete."""

    def __init__(self, sessions_dir: Path) -> None:
        self.store = SessionStore(sessions_dir)

    def create(self) -> str:
        """Create a new session and return its id."""
        session_id = str(uuid.uuid4())[:SESSION_ID_LEN]
        self.store.create(session_id)
        return session_id

    def load(self, session_id: str) -> list[dict]:
        """Load conversation history for a session."""
        return self.store.load(session_id)

    def append_message(self, session_id: str, message: dict) -> None:
        """Persist a single message to the session."""
        self.store.append(session_id, message)

    def overwrite(self, session_id: str, messages: list[dict]) -> None:
        """Rewrite the full conversation history for a session."""
        self.store.overwrite(session_id, messages)

    def runtime_state_path(self, session_id: str) -> Path | None:
        """Return the runtime sidecar path for a session, if it exists."""
        return self.store.runtime_state_path(session_id)

    def save_runtime_state(self, session_id: str, payload: dict[str, Any]) -> None:
        """Persist runtime state for a session."""
        self.store.save_runtime_state(session_id, payload)

    def load_runtime_state(self, session_id: str) -> dict[str, Any] | None:
        """Load runtime state for a session."""
        return self.store.load_runtime_state(session_id)

    def delete_runtime_state(self, session_id: str) -> bool:
        """Delete runtime state for a session."""
        return self.store.delete_runtime_state(session_id)

    def update_message(
        self,
        session_id: str,
        message_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Update a persisted message in place."""
        return self.store.update_message(session_id, message_id, updater)

    def list_all(self) -> list[SessionMeta]:
        """List all sessions sorted by last modified descending."""
        return self.store.list_sessions()

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if found."""
        return self.store.delete(session_id)

    def get_latest(self) -> str | None:
        """Return the session_id of the most recently modified session."""
        sessions = self.store.list_sessions()
        if not sessions:
            return None
        return sessions[0].session_id

    def exists(self, session_id: str) -> bool:
        """Check whether a session file exists."""
        return self.store._find_path(session_id) is not None
