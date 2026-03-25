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


def test_runtime_sidecar_round_trip(store: SessionStore) -> None:
    store.create("sess-1")
    payload = {
        "version": 1,
        "state": "awaiting_user_answer",
        "assistant_message_id": "msg-assistant-1",
        "pending_question": {"question_id": "ask-1", "prompt": "Which task?"},
    }

    store.save_runtime_state("sess-1", payload)

    assert store.load_runtime_state("sess-1") == payload


def test_update_message_rewrites_matching_message(store: SessionStore) -> None:
    store.create("sess-1")
    store.append("sess-1", {"role": "assistant", "message_id": "msg-a", "content": "hel"})

    store.update_message("sess-1", "msg-a", lambda item: {**item, "content": "hello"})

    assert store.load("sess-1")[0]["content"] == "hello"


def test_update_message_preserves_records_before_last_compaction(store: SessionStore) -> None:
    store.create("sess-1")
    store.append("sess-1", {"role": "user", "message_id": "msg-user-1", "content": "before compact"})
    store.append(
        "sess-1",
        {
            "role": "assistant",
            "message_id": "msg-compact",
            "content": "**[SYSTEM: Context Compacted]**",
            "is_compaction": True,
        },
    )
    store.append("sess-1", {"role": "assistant", "message_id": "msg-a", "content": "hel"})

    store.update_message("sess-1", "msg-a", lambda item: {**item, "content": "hello"})

    path = store._find_path("sess-1")
    assert path is not None
    raw_messages = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert raw_messages[0]["message_id"] == "msg-user-1"
    assert raw_messages[1]["message_id"] == "msg-compact"
    assert raw_messages[2]["content"] == "hello"
    assert store.load("sess-1") == raw_messages[1:]


def test_delete_session_also_removes_runtime_sidecar(store: SessionStore) -> None:
    store.create("sess-1")
    store.save_runtime_state("sess-1", {"version": 1, "state": "streaming"})
    runtime_path = store.runtime_state_path("sess-1")
    assert runtime_path is not None
    assert runtime_path.exists()

    assert store.delete("sess-1") is True
    assert not runtime_path.exists()


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
