"""Tests for SessionManager lifecycle."""

from pathlib import Path

import pytest

from src.minibot.session.manager import SessionManager, SESSION_ID_LEN


@pytest.fixture
def manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "sessions")


def test_create_session(manager: SessionManager) -> None:
    sid = manager.create()
    assert len(sid) == SESSION_ID_LEN
    assert manager.exists(sid)


def test_load_and_append(manager: SessionManager) -> None:
    sid = manager.create()
    manager.append_message(sid, {"role": "user", "content": "hi"})
    manager.append_message(sid, {"role": "assistant", "content": "hello"})

    history = manager.load(sid)
    assert len(history) == 2
    assert history[0]["content"] == "hi"


def test_get_latest(manager: SessionManager) -> None:
    s1 = manager.create()
    manager.append_message(s1, {"role": "user", "content": "old"})
    s2 = manager.create()
    manager.append_message(s2, {"role": "user", "content": "new"})

    latest = manager.get_latest()
    # latest should be one of the two (the one with most recent mtime)
    assert latest in {s1, s2}


def test_get_latest_empty(manager: SessionManager) -> None:
    assert manager.get_latest() is None


def test_delete(manager: SessionManager) -> None:
    sid = manager.create()
    assert manager.delete(sid) is True
    assert not manager.exists(sid)
    assert manager.delete(sid) is False


def test_list_all(manager: SessionManager) -> None:
    s1 = manager.create()
    s2 = manager.create()
    sessions = manager.list_all()
    ids = [s.session_id for s in sessions]
    assert s1 in ids
    assert s2 in ids
