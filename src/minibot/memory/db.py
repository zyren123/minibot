"""Memory-only SQLModel engine and session bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session, create_engine

from ..config.schema import MemoryConfig
from . import db_models as _db_models  # noqa: F401


@dataclass
class MemoryDatabase:
    engine: object
    session_factory: sessionmaker
    database_path: Path | None


def _resolve_memory_dir(memory_dir: str, *, app_home: Path) -> Path:
    path = Path(memory_dir).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (app_home / path).resolve()


def _normalize_postgres_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def create_memory_db(config: MemoryConfig, *, app_home: Path) -> MemoryDatabase:
    backend = (config.backend or "sqlite").strip().lower()

    if backend == "sqlite":
        memory_dir = _resolve_memory_dir(config.memory_dir, app_home=app_home)
        memory_dir.mkdir(parents=True, exist_ok=True)
        database_path = memory_dir / "memory_v2.sqlite3"
        engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    elif backend == "postgres":
        if not config.database_url:
            raise ValueError("memory.database_url is required for postgres backend")
        database_path = None
        engine = create_engine(_normalize_postgres_database_url(config.database_url))
    else:
        raise ValueError(f"Unsupported memory backend: {config.backend}")

    SQLModel.metadata.create_all(engine)
    return MemoryDatabase(
        engine=engine,
        session_factory=sessionmaker(bind=engine, class_=Session, expire_on_commit=False),
        database_path=database_path,
    )
