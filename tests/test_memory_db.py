import pytest
from sqlmodel import SQLModel

from src.minibot.config.schema import MemoryConfig
from src.minibot.memory import db as memory_db
from src.minibot.memory.db import create_memory_db


def test_sqlmodel_db_helper_bootstraps_sqlite_memory_tables(tmp_path):
    db = create_memory_db(
        MemoryConfig(enabled=True, memory_dir=str(tmp_path / "memory"), backend="sqlite"),
        app_home=tmp_path / ".minibot",
    )

    assert db.database_path is not None
    assert db.database_path.name == "memory_v2.sqlite3"
    assert db.database_path.exists()
    assert db.engine is not None


def test_postgres_backend_requires_database_url(tmp_path):
    with pytest.raises(ValueError):
        create_memory_db(
            MemoryConfig(
                enabled=True,
                memory_dir=str(tmp_path / "memory"),
                backend="postgres",
                database_url=None,
            ),
            app_home=tmp_path / ".minibot",
        )


def test_postgres_backend_defaults_plain_postgresql_urls_to_psycopg(tmp_path, monkeypatch):
    captured: dict[str, str] = {}

    def fake_create_engine(url: str, *args, **kwargs):
        captured["url"] = url
        return object()

    monkeypatch.setattr("src.minibot.memory.db.create_engine", fake_create_engine)
    monkeypatch.setattr(SQLModel.metadata, "create_all", lambda engine: None)

    create_memory_db(
        MemoryConfig(
            enabled=True,
            memory_dir=str(tmp_path / "memory"),
            backend="postgres",
            database_url="postgresql://writer:secret@localhost:5432/minibot_memory",
        ),
        app_home=tmp_path / ".minibot",
    )

    assert captured["url"] == "postgresql+psycopg://writer:secret@localhost:5432/minibot_memory"


def test_create_memory_db_reuses_cached_postgres_engine_for_same_url(tmp_path, monkeypatch):
    created_urls: list[str] = []
    create_all_calls: list[object] = []

    def fake_create_engine(url: str, *args, **kwargs):
        engine = object()
        created_urls.append(url)
        return engine

    monkeypatch.setattr("src.minibot.memory.db.create_engine", fake_create_engine)
    monkeypatch.setattr(SQLModel.metadata, "create_all", lambda engine: create_all_calls.append(engine))
    if hasattr(memory_db, "_clear_memory_db_cache"):
        memory_db._clear_memory_db_cache()

    config = MemoryConfig(
        enabled=True,
        memory_dir=str(tmp_path / "memory"),
        backend="postgres",
        database_url="postgresql://writer:secret@localhost:5432/minibot_memory",
    )

    first = create_memory_db(config, app_home=tmp_path / ".minibot")
    second = create_memory_db(config, app_home=tmp_path / ".minibot")

    assert first is second
    assert created_urls == ["postgresql+psycopg://writer:secret@localhost:5432/minibot_memory"]
    assert create_all_calls == [first.engine]
