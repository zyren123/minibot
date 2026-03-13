"""Tests for SessionStore JSONL persistence."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.minibot.session.store import SessionStore, PREVIEW_MAX_LEN


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


def test_append_and_load(store: SessionStore) -> None:
    sid = "abcd1234"
    store.create(sid)
    store.append(sid, {"role": "user", "content": "hello"})
    store.append(sid, {"role": "assistant", "content": "hi"})

    messages = store.load(sid)
    assert len(messages) == 2
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1] == {"role": "assistant", "content": "hi"}


def test_load_nonexistent(store: SessionStore) -> None:
    assert store.load("nonexistent") == []


def test_list_sessions(store: SessionStore) -> None:
    store.create("aaa")
    store.append("aaa", {"role": "user", "content": "first"})
    store.create("bbb")
    store.append("bbb", {"role": "user", "content": "second"})

    sessions = store.list_sessions()
    ids = [s.session_id for s in sessions]
    assert "aaa" in ids
    assert "bbb" in ids
    for s in sessions:
        assert s.message_count >= 1


def test_delete_session(store: SessionStore) -> None:
    store.create("del-me")
    store.append("del-me", {"role": "user", "content": "bye"})
    assert store.delete("del-me") is True
    assert store.load("del-me") == []
    assert store.delete("del-me") is False


def test_session_meta_preview(store: SessionStore) -> None:
    sid = "prev"
    store.create(sid)
    long_text = "x" * 100
    store.append(sid, {"role": "user", "content": long_text})

    sessions = store.list_sessions()
    meta = next(s for s in sessions if s.session_id == sid)
    assert len(meta.preview) <= PREVIEW_MAX_LEN
    assert meta.preview.endswith("...")


def test_date_subdir_structure(store: SessionStore) -> None:
    sid = "dated"
    dt = datetime(2026, 3, 13, 10, 30)
    path = store.create(sid, date=dt)
    assert "2026/03/13" in str(path)
    assert path.name == "dated.jsonl"


def test_jsonl_format(store: SessionStore) -> None:
    sid = "fmt"
    store.create(sid)
    msg = {"role": "user", "content": "test 中文"}
    store.append(sid, msg)

    path = store._find_path(sid)
    assert path is not None
    raw = path.read_text(encoding="utf-8").strip()
    assert json.loads(raw) == msg
