"""SQLite journal for durable agent execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Literal, TypeVar
from uuid import UUID, uuid4

from .tool_args import parse_tool_arguments
from .workspace import DockerContainer, WorkspaceIdentity


RunStatus = Literal[
    "created", "running", "waiting", "waiting_child", "completed", "failed", "interrupted",
    "recovery_required", "abandoned",
]
StepStatus = Literal["planned", "waiting", "waiting_child", "executing", "completed", "recovery_required"]
WaitReason = Literal["approval", "user_question", "child"]
Message = dict[str, Any]
_T = TypeVar("_T")
_TERMINAL = ("completed", "failed", "abandoned")
_CHECKPOINT_VERSION = 1
_UNKNOWN_EFFECT = "The process stopped before recording the effects of this step."


class DurableRunError(RuntimeError):
    """The requested durable operation cannot proceed."""


class DurableRunBusy(DurableRunError):
    """The run still has an execution owner."""


class DurableRunPaused(DurableRunError):
    """The run stopped at a safe boundary after a pause request."""


@dataclass(frozen=True)
class RunState:
    run_id: str
    workspace_id: str
    session_id: str
    sessions_dir: str
    status: RunStatus
    segment: int
    created_at: str
    updated_at: str
    parent_run_id: str | None
    owner_pid: int | None
    pause_requested: bool
    wait_reason: WaitReason | None
    error: str | None


@dataclass(frozen=True)
class ChildRun:
    run: RunState
    parent_step_id: str
    workspace_path: Path
    execution: dict[str, Any]
    ordinal: int = 0


@dataclass(frozen=True)
class StepState:
    step_id: str
    run_id: str
    ordinal: int
    assistant_message_id: str
    kind: Literal["tool"]
    status: StepStatus
    provider_call_id: str
    name: str
    arguments: str
    started_at: str | None
    completed_at: str | None
    retryable: bool
    result: Message | None
    question: Message | None
    artifact_id: str | None
    error: str | None
    child_run_id: str | None


@dataclass(frozen=True)
class RuntimeCheckpoint:
    version: int
    messages: list[Message]
    assistant_message: Message | None = None
    finished: bool = False
    user_intent: str | None = None
    reasoning_effort: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _canonical_id(run_id: str) -> str:
    try:
        return UUID(run_id).hex
    except (ValueError, TypeError, AttributeError) as exc:
        raise DurableRunError("Invalid durable run ID.") from exc


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            raise DurableRunBusy("A process is still running this durable run.") from exc
        yield
    finally:
        os.close(descriptor)


class RunStore:
    """Own runs by ID and scope. SQLite commits state before the next side effect."""

    def __init__(self, app_home: Path, identity: WorkspaceIdentity, sessions_dir: Path) -> None:
        self.root = app_home.resolve() / "runs"
        self.workspace_id = identity.value
        self.sessions_dir = str(sessions_dir.resolve())
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or (
            os.name == "posix" and (self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077)
        ):
            raise DurableRunError("Durable run storage is not private.")
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    sessions_dir TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    segment INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    parent_run_id TEXT,
                    owner_pid INTEGER,
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    wait_reason TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS runs_scope
                ON runs(workspace_id, sessions_dir, session_id, created_at);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    version INTEGER NOT NULL,
                    messages TEXT NOT NULL,
                    assistant_message TEXT,
                    finished INTEGER NOT NULL DEFAULT 0,
                    user_intent TEXT,
                    reasoning_effort TEXT
                );
                CREATE TABLE IF NOT EXISTS steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    ordinal INTEGER NOT NULL,
                    assistant_message_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_call_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0,
                    result TEXT,
                    question TEXT,
                    artifact_id TEXT,
                    error TEXT,
                    child_run_id TEXT,
                    UNIQUE(run_id, ordinal)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS steps_child_run ON steps(child_run_id)
                WHERE child_run_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS child_runs (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    parent_step_id TEXT NOT NULL REFERENCES steps(step_id),
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    workspace_path TEXT NOT NULL UNIQUE,
                    execution TEXT NOT NULL,
                    UNIQUE(parent_step_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS child_containers (
                    run_id TEXT PRIMARY KEY REFERENCES child_runs(run_id),
                    name TEXT NOT NULL UNIQUE,
                    container_id TEXT
                );
                """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.root / "journal.sqlite3")) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            with db:
                yield db

    @staticmethod
    def _run(row: sqlite3.Row | dict[str, Any]) -> RunState:
        return RunState(**{**dict(row), "pause_requested": bool(row["pause_requested"])})

    @staticmethod
    def _step(row: sqlite3.Row) -> StepState:
        return StepState(**{
            **dict(row), "retryable": bool(row["retryable"]),
            "result": json.loads(row["result"]) if row["result"] is not None else None,
            "question": json.loads(row["question"]) if row["question"] is not None else None,
        })

    def _find(self, db: sqlite3.Connection, session_id: str, run_id: str) -> RunState:
        row = db.execute(
            "SELECT * FROM runs WHERE run_id = ? AND workspace_id = ? AND sessions_dir = ? AND session_id = ?",
            (run_id, self.workspace_id, self.sessions_dir, session_id),
        ).fetchone()
        if row is None:
            raise DurableRunError("Durable run is unavailable in this workspace and session.")
        return self._run(row)

    def _session_lock(self, session_id: str):
        scope = sha256(_encode([self.sessions_dir, session_id]).encode("ascii")).hexdigest()
        return _process_lock(self.root / f"{scope}.lock")

    def _require_resolved_session(self, db: sqlite3.Connection, session_id: str) -> None:
        unfinished = db.execute(
            "SELECT run_id FROM runs WHERE sessions_dir = ? AND session_id = ? "
            "AND status NOT IN ('completed', 'failed', 'abandoned')",
            (self.sessions_dir, session_id),
        ).fetchone()
        if unfinished is not None:
            raise DurableRunError(f"Resume or abandon unfinished durable run {unfinished['run_id']} first.")

    @contextmanager
    def guard_session_mutation(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            with self._connection() as db:
                self._require_resolved_session(db, session_id)
            yield

    @contextmanager
    def claim(self, session_id: str, run_id: str, *, new: bool = False) -> Iterator["RunJournal"]:
        run_id = _canonical_id(run_id)
        if not new:
            with self._connection() as db:
                self._find(db, session_id, run_id)
        with self._session_lock(session_id), _process_lock(self.root / f"{run_id}.lock"):
            yield RunJournal(self, session_id, run_id)

    def list_runs(self, session_id: str, *, incomplete_only: bool = False) -> list[RunState]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM runs WHERE workspace_id = ? AND sessions_dir = ? "
                "AND session_id = ? ORDER BY created_at DESC, run_id DESC",
                (self.workspace_id, self.sessions_dir, session_id),
            ).fetchall()
        runs = []
        for row in rows:
            run = self._run(row)
            if run.owner_pid is not None:
                with suppress(DurableRunBusy):
                    with _process_lock(self.root / f"{run.run_id}.lock"):
                        journal = RunJournal(self, session_id, run.run_id)
                        run = journal.recover()
            if not incomplete_only or run.status not in _TERMINAL:
                runs.append(run)
        return runs

    def list_steps(self, session_id: str, run_id: str) -> list[StepState]:
        run_id = _canonical_id(run_id)
        with self._connection() as db:
            self._find(db, session_id, run_id)
            rows = db.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY ordinal", (run_id,)).fetchall()
        return [self._step(row) for row in rows]

    def _children(
        self, db: sqlite3.Connection, parent_run_id: str, *,
        step_id: str | None = None, child_run_id: str | None = None, ordinal: int | None = None,
    ) -> list[ChildRun]:
        rows = db.execute(
            "SELECT runs.*, child_runs.parent_step_id, child_runs.workspace_path, child_runs.execution, "
            "child_runs.ordinal AS child_ordinal "
            "FROM steps JOIN child_runs ON child_runs.parent_step_id = steps.step_id "
            "JOIN runs ON runs.run_id = child_runs.run_id AND runs.parent_run_id = steps.run_id "
            "WHERE steps.run_id = ? AND (? IS NULL OR steps.step_id = ?) "
            "AND (? IS NULL OR runs.run_id = ?) AND (? IS NULL OR child_runs.ordinal = ?) "
            "ORDER BY steps.ordinal, child_runs.ordinal",
            (parent_run_id, step_id, step_id, child_run_id, child_run_id, ordinal, ordinal),
        ).fetchall()
        children = []
        for row in rows:
            fields = dict(row)
            step = fields.pop("parent_step_id")
            path = Path(fields.pop("workspace_path"))
            execution = json.loads(fields.pop("execution"))
            child_ordinal = fields.pop("child_ordinal")
            children.append(ChildRun(self._run(fields), step, path, execution, child_ordinal))
        return children

    def list_children(self, session_id: str, parent_run_id: str) -> list[ChildRun]:
        """List child runs belonging to a parent in this workspace and session."""
        parent_run_id = _canonical_id(parent_run_id)
        with self._connection() as db:
            self._find(db, session_id, parent_run_id)
            return self._children(db, parent_run_id)

    def get_child(self, session_id: str, parent_run_id: str, child_run_id: str) -> ChildRun:
        parent_run_id, child_run_id = _canonical_id(parent_run_id), _canonical_id(child_run_id)
        with self._connection() as db:
            self._find(db, session_id, parent_run_id)
            children = self._children(db, parent_run_id, child_run_id=child_run_id)
        if not children:
            raise DurableRunError("Child run is unavailable for this parent run.")
        return children[0]

    def for_child(self, child: ChildRun) -> "RunStore":
        """Open the child journal scope in the same database."""
        return RunStore(self.root.parent, WorkspaceIdentity(child.run.workspace_id), Path(child.run.sessions_dir))

    def pause(self, session_id: str, run_id: str) -> bool:
        run_id = _canonical_id(run_id)
        with self._connection() as db:
            self._find(db, session_id, run_id)
            return bool(db.execute(
                "UPDATE runs SET pause_requested = 1, updated_at = ? WHERE run_id = ? "
                "AND owner_pid IS NOT NULL AND status IN ('running', 'waiting', 'waiting_child')",
                (_now(), run_id),
            ).rowcount)


class RunJournal:
    def __init__(self, store: RunStore, session_id: str, run_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.run_id = run_id

    def create(
        self, messages: list[Message], parent_run_id: str | None, reasoning_effort: str | None,
    ) -> None:
        with self.store._connection() as db:
            self.store._require_resolved_session(db, self.session_id)
            self._insert_run(
                db, self.run_id, self.session_id, self.store.workspace_id, messages, parent_run_id, reasoning_effort,
            )

    def _insert_run(
        self, db: sqlite3.Connection, run_id: str, session_id: str, workspace_id: str,
        messages: list[Message], parent_run_id: str | None, reasoning_effort: str | None = None,
    ) -> None:
        now = _now()
        db.execute(
            "INSERT INTO runs(run_id, workspace_id, sessions_dir, session_id, status, created_at, "
            "updated_at, parent_run_id) VALUES (?, ?, ?, ?, 'created', ?, ?, ?)",
            (run_id, workspace_id, self.store.sessions_dir, session_id, now, now, parent_run_id),
        )
        user_intent = next(
            (message.get("content") for message in reversed(messages) if message.get("role") == "user"), None,
        )
        db.execute(
            "INSERT INTO checkpoints(run_id, version, messages, user_intent, reasoning_effort) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, _CHECKPOINT_VERSION, _encode(messages),
             user_intent if isinstance(user_intent, str) else None, reasoning_effort),
        )

    def child_for_step(self, step: StepState, *, ordinal: int = 0) -> ChildRun | None:
        with self.store._connection() as db:
            children = self.store._children(db, self.run_id, step_id=step.step_id, ordinal=ordinal)
        return children[0] if children else None

    def create_child(
        self, step: StepState, *, child_run_id: str, workspace_path: Path, workspace_identity: WorkspaceIdentity,
        execution: dict[str, Any], messages: list[Message], ordinal: int = 0,
    ) -> ChildRun:
        """Commit the child identity and its parent step before the child can run."""
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self.store._children(db, self.run_id, step_id=step.step_id, ordinal=ordinal)
            if existing:
                return existing[0]
            child_run_id = _canonical_id(child_run_id)
            if child_run_id == self.session_id:
                raise DurableRunError("Child run requires a separate session.")
            self._wait_for_child(db)
            self.store._require_resolved_session(db, child_run_id)
            self._insert_run(db, child_run_id, child_run_id, workspace_identity.value, messages, self.run_id)
            if not db.execute(
                "UPDATE steps SET status = 'waiting_child', child_run_id = COALESCE(child_run_id, ?), "
                "started_at = COALESCE(started_at, ?) WHERE step_id = ? AND run_id = ? "
                "AND status IN ('planned', 'waiting', 'executing', 'waiting_child')",
                (child_run_id, _now(), step.step_id, self.run_id),
            ).rowcount:
                raise DurableRunError("Parent step is unavailable for child delegation.")
            db.execute(
                "INSERT INTO child_runs(run_id, parent_step_id, ordinal, workspace_path, execution) VALUES (?, ?, ?, ?, ?)",
                (child_run_id, step.step_id, ordinal, str(workspace_path.resolve()), _encode(execution)),
            )
            [child] = self.store._children(db, self.run_id, step_id=step.step_id, ordinal=ordinal)
            return child

    def wait_for_child(self, step: StepState) -> None:
        """Continue waiting on a child without reopening the parent tool step."""
        with self.store._connection() as db:
            if not db.execute(
                "SELECT 1 FROM steps WHERE step_id = ? AND run_id = ? AND status = 'waiting_child'",
                (step.step_id, self.run_id),
            ).fetchone():
                raise DurableRunError("Parent step has no pending child run.")
            self._wait_for_child(db)

    def _wait_for_child(self, db: sqlite3.Connection) -> None:
        if not db.execute(
            "UPDATE runs SET status = 'waiting_child', wait_reason = 'child', updated_at = ? "
            "WHERE run_id = ? AND owner_pid IS NOT NULL AND pause_requested = 0",
            (_now(), self.run_id),
        ).rowcount:
            raise DurableRunPaused("Paused at a durable execution boundary.")

    def _waiting_child(self, db: sqlite3.Connection) -> bool:
        return db.execute(
            "SELECT 1 FROM steps WHERE run_id = ? AND status = 'waiting_child' LIMIT 1", (self.run_id,),
        ).fetchone() is not None

    def state(self) -> RunState:
        with self.store._connection() as db:
            return self.store._find(db, self.session_id, self.run_id)

    def checkpoint(self) -> RuntimeCheckpoint:
        with self.store._connection() as db:
            row = db.execute("SELECT * FROM checkpoints WHERE run_id = ?", (self.run_id,)).fetchone()
        if row["version"] != _CHECKPOINT_VERSION:
            raise DurableRunError(f"Unsupported runtime checkpoint version {row['version']}.")
        assistant = row["assistant_message"]
        return RuntimeCheckpoint(
            version=row["version"], messages=json.loads(row["messages"]),
            assistant_message=json.loads(assistant) if assistant is not None else None,
            finished=bool(row["finished"]), user_intent=row["user_intent"], reasoning_effort=row["reasoning_effort"],
        )

    def recover(self, *, retry_allowed: Callable[[str], bool] | None = None) -> RunState:
        run = self.state()
        if run.status in _TERMINAL:
            return run
        if run.owner_pid is None and retry_allowed is None:
            return run
        with self.store._connection() as db:
            rows = db.execute(
                "SELECT step_id, name, retryable FROM steps WHERE run_id = ? AND status = 'executing' "
                "AND child_run_id IS NULL",
                (self.run_id,),
            ).fetchall()
            for row in rows:
                retryable = bool(row["retryable"])
                if retry_allowed is not None and retryable and retry_allowed(row["name"]):
                    db.execute("UPDATE steps SET status = 'planned' WHERE step_id = ?", (row["step_id"],))
                elif not retryable or retry_allowed is not None:
                    db.execute(
                        "UPDATE steps SET status = 'recovery_required', error = ? WHERE step_id = ?",
                        (_UNKNOWN_EFFECT, row["step_id"]),
                    )
            db.execute(
                "UPDATE steps SET status = 'planned' WHERE run_id = ? AND status = 'waiting' AND question IS NULL",
                (self.run_id,),
            )
            requires_recovery = db.execute(
                "SELECT 1 FROM steps WHERE run_id = ? AND status = 'recovery_required'", (self.run_id,),
            ).fetchone() is not None
            if requires_recovery:
                self._set_status(db, "recovery_required", error=_UNKNOWN_EFFECT)
            elif run.status == "waiting" and run.wait_reason == "user_question":
                self._set_status(db, "waiting", wait_reason="user_question")
            elif self._waiting_child(db):
                self._set_status(db, "waiting_child", wait_reason="child")
            else:
                self._set_status(db, "interrupted")
        return self.state()

    def start_segment(self) -> int:
        with self.store._connection() as db:
            waiting_child = self._waiting_child(db)
            db.execute(
                "UPDATE runs SET status = ?, segment = segment + 1, owner_pid = ?, "
                "pause_requested = 0, wait_reason = ?, error = NULL, updated_at = ? WHERE run_id = ?",
                ("waiting_child" if waiting_child else "running", os.getpid(),
                 "child" if waiting_child else None, _now(), self.run_id),
            )
        return self.state().segment

    def end_segment(self, status: RunStatus, *, error: str | None = None) -> None:
        run = self.state()
        if run.status == "completed":
            return
        if status != "completed":
            recovered = self.recover()
            if recovered.status in {"recovery_required", "waiting_child"}:
                return
            if status == "waiting" and recovered.wait_reason == "user_question":
                return
        with self.store._connection() as db:
            self._set_status(db, status, error=error)

    def _set_status(
        self, db: sqlite3.Connection, status: RunStatus, *,
        wait_reason: WaitReason | None = None, error: str | None = None,
    ) -> None:
        db.execute(
            "UPDATE runs SET status = ?, owner_pid = NULL, wait_reason = ?, error = ?, "
            "pause_requested = 0, updated_at = ? WHERE run_id = ?",
            (status, wait_reason, error, _now(), self.run_id),
        )

    def check_pause(self) -> None:
        if self.state().pause_requested:
            raise DurableRunPaused("Paused at a durable execution boundary.")

    def require_recovery(self, error: str) -> None:
        with self.store._connection() as db:
            if self.store._find(db, self.session_id, self.run_id).status not in _TERMINAL:
                self._set_status(db, "recovery_required", error=error)

    def pending_container(self) -> DockerContainer | None:
        with self.store._connection() as db:
            row = db.execute(
                "SELECT name, container_id FROM child_containers WHERE run_id = ?", (self.run_id,),
            ).fetchone()
        return DockerContainer(**dict(row)) if row is not None else None

    def reserve_container(self, name: str) -> None:
        with self.store._connection() as db:
            db.execute("INSERT INTO child_containers(run_id, name) VALUES (?, ?)", (self.run_id, name))

    def confirm_container(self, name: str, container_id: str) -> None:
        with self.store._connection() as db:
            if db.execute(
                "UPDATE child_containers SET container_id = ? WHERE run_id = ? AND name = ? AND container_id IS NULL",
                (container_id, self.run_id, name),
            ).rowcount != 1:
                raise DurableRunError("Docker lease is unavailable.")

    def release_container(self, name: str) -> None:
        with self.store._connection() as db:
            if db.execute(
                "DELETE FROM child_containers WHERE run_id = ? AND name = ?", (self.run_id, name),
            ).rowcount != 1:
                raise DurableRunError("Docker lease is unavailable.")

    def plan_batch(
        self, messages: list[Message], assistant: Message,
        calls: list[tuple[str, str, str]],
    ) -> list[StepState]:
        batch_id = assistant["message_id"]
        with self.store._connection() as db:
            db.execute(
                "UPDATE checkpoints SET messages = ?, assistant_message = ? WHERE run_id = ?",
                (_encode(messages), _encode(assistant), self.run_id),
            )
            ordinal = db.execute(
                "SELECT COALESCE(MAX(ordinal), 0) FROM steps WHERE run_id = ?", (self.run_id,),
            ).fetchone()[0]
            db.executemany(
                "INSERT INTO steps(step_id, run_id, ordinal, assistant_message_id, kind, status, "
                "provider_call_id, name, arguments) VALUES (?, ?, ?, ?, 'tool', 'planned', ?, ?, ?)",
                [(uuid4().hex, self.run_id, ordinal + i, batch_id, *call)
                 for i, call in enumerate(calls, start=1)],
            )
        return self.batch_steps(batch_id)

    def batch_steps(self, message_id: str) -> list[StepState]:
        with self.store._connection() as db:
            rows = db.execute(
                "SELECT * FROM steps WHERE run_id = ? AND assistant_message_id = ? ORDER BY ordinal",
                (self.run_id, message_id),
            ).fetchall()
        return [self.store._step(row) for row in rows]

    def save_messages(self, messages: list[Message], *, complete: bool = False) -> None:
        with self.store._connection() as db:
            db.execute(
                "UPDATE checkpoints SET messages = ?, assistant_message = NULL, finished = ? WHERE run_id = ?",
                (_encode(messages), complete, self.run_id),
            )

    def begin_step(self, step: StepState, *, retryable: bool) -> None:
        with self.store._connection() as db:
            if not self._mark_active(db, check_pause=True):
                raise DurableRunPaused("Paused at a durable execution boundary.")
            if not db.execute(
                "UPDATE steps SET status = 'executing', retryable = ?, started_at = COALESCE(started_at, ?) "
                "WHERE step_id = ? AND run_id = ? AND child_run_id IS NULL",
                (retryable, _now(), step.step_id, self.run_id),
            ).rowcount:
                raise DurableRunError("Tool step is unavailable for execution.")

    def _mark_active(self, db: sqlite3.Connection, *, check_pause: bool = False) -> bool:
        waiting_child = self._waiting_child(db)
        return bool(db.execute(
            "UPDATE runs SET status = ?, wait_reason = ?, updated_at = ? "
            "WHERE run_id = ? AND (NOT ? OR pause_requested = 0)",
            ("waiting_child" if waiting_child else "running", "child" if waiting_child else None,
             _now(), self.run_id, check_pause),
        ).rowcount)

    def finish_step(
        self, step: StepState, result: Message, *, artifact_id: str | None = None, error: str | None = None,
    ) -> None:
        with self.store._connection() as db:
            self._finish_step(db, step.step_id, result, artifact_id=artifact_id, error=error)
            self._mark_active(db)

    @staticmethod
    def _finish_step(
        db: sqlite3.Connection, step_id: str, result: Message, *,
        artifact_id: str | None = None, error: str | None = None,
    ) -> None:
        now = _now()
        db.execute(
            "UPDATE steps SET status = 'completed', completed_at = ?, started_at = COALESCE(started_at, ?), "
            "result = ?, artifact_id = ?, error = ? WHERE step_id = ?",
            (now, now, _encode(result), artifact_id, error, step_id),
        )

    async def wait(
        self, step: StepState, reason: WaitReason, operation: Callable[[], Awaitable[_T]],
        *, question: Message | None = None,
    ) -> _T:
        with self.store._connection() as db:
            if not db.execute(
                "UPDATE steps SET status = 'waiting', question = ?, "
                "started_at = COALESCE(started_at, ?) WHERE step_id = ? AND run_id = ? AND child_run_id IS NULL",
                (_encode(question) if question is not None else None, _now(), step.step_id, self.run_id),
            ).rowcount:
                raise DurableRunError("Tool step is unavailable for approval or a question.")
            db.execute(
                "UPDATE runs SET status = 'waiting', wait_reason = ?, updated_at = ? WHERE run_id = ?",
                (reason, _now(), self.run_id),
            )
        self.check_pause()
        pending = asyncio.ensure_future(operation())
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=0.25)
                self.check_pause()
            return pending.result()
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    def question_step(self, question_id: object) -> StepState | None:
        if not isinstance(question_id, str):
            return None
        with self.store._connection() as db:
            row = db.execute(
                "SELECT * FROM steps WHERE run_id = ? AND step_id = ? AND question IS NOT NULL "
                "AND status IN ('waiting', 'completed')", (self.run_id, question_id),
            ).fetchone()
        return self.store._step(row) if row is not None else None

    def resolve(self, step_id: str, output: str, *, is_error: bool) -> None:
        run = self.recover()
        if run.status != "recovery_required":
            raise DurableRunError("The durable run is not waiting for recovery.")
        with self.store._connection() as db:
            row = db.execute(
                "SELECT * FROM steps WHERE run_id = ? AND step_id = ? AND status = 'recovery_required'",
                (self.run_id, step_id),
            ).fetchone()
            if row is None:
                raise DurableRunError("The step is not waiting for recovery in this run.")
            step = self.store._step(row)
            assistant = self.checkpoint().assistant_message
            args, _ = parse_tool_arguments(step.arguments)
            self._finish_step(db, step.step_id, {
                "role": "tool", "message_id": f"msg-{uuid4().hex[:12]}",
                "parent_user_message_id": assistant.get("parent_user_message_id"),
                "tool_call_id": step.provider_call_id, "tool_name": step.name, "tool_args": args or {},
                "content": output, "tool_status": "error" if is_error else "success", "is_error": is_error,
            }, error=output if is_error else None)
            self._set_status(db, "interrupted")

    def abandon(self) -> None:
        run = self.state()
        if run.status in _TERMINAL:
            raise DurableRunError("The durable run has already ended.")
        with self.store._connection() as db:
            self._set_status(db, "abandoned")
