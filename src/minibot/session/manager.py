"""Session lifecycle management."""

from __future__ import annotations

import uuid
from pathlib import Path

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
