"""Single-file checkpoints backed by an application-owned Git object store."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import TemporaryDirectory
from typing import Literal, TypeVar
from uuid import uuid4


MAX_FILE_BYTES = 16 * 1024 * 1024
_CHECKPOINTS = "refs/minibot/checkpoints"
_ROLLED_BACK = "refs/minibot/rolled-back"
_ROLLBACK_STARTED = "refs/minibot/rollback-started"
_T = TypeVar("_T")


class FileCheckpointError(RuntimeError):
    """A file checkpoint operation could not be completed safely."""


@dataclass(frozen=True)
class FileCheckpoint:
    id: str
    path: str
    created_at: str
    created_file: bool
    status: Literal["recorded", "rolled_back", "rollback_incomplete"]

    @property
    def rolled_back(self) -> bool:
        return self.status == "rolled_back"


@dataclass(frozen=True)
class _FileState:
    data: bytes
    mode: int


@dataclass(frozen=True)
class _Record:
    checkpoint: FileCheckpoint
    before_mode: int | None
    after_mode: int


async def finish_file_operation(operation: Callable[[], _T]) -> _T:
    """Finish a started filesystem transaction before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


class GitFileCheckpoints:
    """Record changes and restore only the exact post-state of one checkpoint."""

    def __init__(self, workdir: Path, app_home: Path) -> None:
        self.workdir = workdir.resolve()
        key = hashlib.sha256(os.fsencode(self.workdir)).hexdigest()
        self.storage = app_home.resolve() / "file-checkpoints"
        self.repository = self.storage / f"{key}.git"
        self.lock_path = self.storage / f"{key}.lock"

    @staticmethod
    def _run_git(
        args: list[str], *, cwd: Path, data: bytes | None = None, accepted: tuple[int, ...] = (0,),
    ) -> bytes:
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
        try:
            result = subprocess.run(
                ["git", "--no-pager", *args], cwd=cwd, env=env,
                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False,
            )
        except FileNotFoundError as exc:
            raise FileCheckpointError("Git is required for file checkpoints.") from exc
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise FileCheckpointError(f"Git checkpoint command could not finish: {exc}") from exc
        if result.returncode not in accepted:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise FileCheckpointError(error or f"Git checkpoint command exited with {result.returncode}.")
        return result.stdout

    def _git(self, *args: str, data: bytes | None = None) -> bytes:
        return self._run_git(["--git-dir", str(self.repository), *args], cwd=self.storage, data=data)

    def _validate_storage(self) -> None:
        if self.storage.is_symlink() or self.repository.is_symlink() or self.lock_path.is_symlink():
            raise FileCheckpointError("Checkpoint storage cannot be a symbolic link.")
        if self.storage.resolve() != self.storage:
            raise FileCheckpointError("Checkpoint storage changed location.")

    def _require_worktree(self) -> None:
        try:
            result = self._run_git(["rev-parse", "--is-inside-work-tree"], cwd=self.workdir)
        except FileCheckpointError as exc:
            raise FileCheckpointError(f"File checkpoints require a Git worktree: {exc}") from exc
        if result.strip() != b"true":
            raise FileCheckpointError("File checkpoints require a Git worktree.")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._validate_storage()
        self.storage.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _initialize(self) -> None:
        if not self.repository.exists():
            with TemporaryDirectory(dir=self.storage, prefix=".init-") as directory:
                scratch = Path(directory)
                self._run_git(["init", "--bare", "--quiet", "--template=", "."], cwd=scratch)
                scratch.replace(self.repository)

    def _target(self, path: Path) -> Path:
        if (
            not path.is_relative_to(self.workdir)
            or path == self.workdir
            or path.resolve() != path
            or path.is_relative_to(self.storage)
            or any(part.casefold() == ".git" for part in path.relative_to(self.workdir).parts)
        ):
            raise FileCheckpointError("Checkpoint target must be a regular path inside the workspace.")
        return path

    def _read_target(self, target: Path) -> _FileState | None:
        self._target(target)
        try:
            info = target.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FileCheckpointError("Checkpoint target must be a regular file with one hard link.")
        if info.st_size > MAX_FILE_BYTES:
            raise FileCheckpointError("Checkpoint target exceeds the 16 MiB file limit.")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(target, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(info, opened):
                raise FileCheckpointError("Checkpoint target changed while it was being opened.")
            data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise FileCheckpointError("Checkpoint target exceeds the 16 MiB file limit.")
            return _FileState(data, stat.S_IMODE(opened.st_mode))

    def write(self, target: Path, content: str) -> None:
        self._change(target, lambda _: content)

    def edit(self, target: Path, old_text: str, new_text: str) -> bool:
        def transform(before: _FileState | None) -> str | None:
            if before is None:
                raise FileNotFoundError(f"File does not exist: {target}")
            with io.TextIOWrapper(io.BytesIO(before.data), encoding="utf-8") as stream:
                content = stream.read()
            if old_text not in content:
                return None
            return content.replace(old_text, new_text, 1)

        return self._change(target, transform)

    def _change(self, target: Path, transform: Callable[[_FileState | None], str | None]) -> bool:
        self._require_worktree()
        with self._locked():
            before = self._read_target(target)
            content = transform(before)
            if content is None:
                return False
            data = content.replace("\n", os.linesep).encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise FileCheckpointError("Checkpoint output exceeds the 16 MiB file limit.")
            if before is not None and before.data == data:
                return True
            if before is not None and not os.access(target, os.W_OK):
                raise FileCheckpointError("Checkpoint target is not writable.")
            self._initialize()
            parent = target.parent
            while not parent.exists():
                parent = parent.parent
            with TemporaryDirectory(dir=parent, prefix=".minibot-file-") as directory:
                staged = Path(directory) / "file"
                after = _FileState(data, self._stage(staged, data, before.mode if before else None))
                checkpoint_id = uuid4().hex
                self._save(checkpoint_id, target, before, after)
                try:
                    self._expect(target, before)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(target)
                except Exception:
                    with suppress(FileCheckpointError):
                        self._git("update-ref", "-d", f"{_CHECKPOINTS}/{checkpoint_id}")
                    raise
            return True

    @staticmethod
    def _stage(staged: Path, data: bytes, mode: int | None) -> int:
        with staged.open("xb") as stream:
            stream.write(data)
            if mode is not None:
                staged.chmod(mode)
            stream.flush()
            os.fsync(stream.fileno())
            return stat.S_IMODE(os.fstat(stream.fileno()).st_mode)

    def _expect(self, target: Path, expected: _FileState | None) -> None:
        if self._read_target(target) != expected:
            raise FileCheckpointError("Checkpoint conflict: the target no longer matches the expected state.")

    def _blob(self, data: bytes) -> str:
        return self._git("hash-object", "-w", "--stdin", data=data).decode("ascii").strip()

    def _save(self, checkpoint_id: str, target: Path, before: _FileState | None, after: _FileState) -> None:
        metadata = {
            "path": target.relative_to(self.workdir).as_posix(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "before_mode": None if before is None else before.mode,
            "after_mode": after.mode,
        }
        objects = {"metadata": self._blob(json.dumps(metadata).encode("utf-8")), "after": self._blob(after.data)}
        if before is not None:
            objects["before"] = self._blob(before.data)
        tree_entries = "".join(f"100644 blob {oid}\t{name}\0" for name, oid in objects.items())
        tree = self._git("mktree", "-z", data=tree_entries.encode("ascii")).decode("ascii").strip()
        self._git("update-ref", f"{_CHECKPOINTS}/{checkpoint_id}", tree)

    @staticmethod
    def _validate_id(checkpoint_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id):
            raise FileCheckpointError("Invalid file checkpoint ID.")

    def _record(self, checkpoint_id: str, rolled_back: set[str], rollback_started: set[str]) -> _Record:
        ref = f"{_CHECKPOINTS}/{checkpoint_id}"
        metadata = json.loads(self._git("cat-file", "blob", f"{ref}:metadata").decode("utf-8"))
        status: Literal["recorded", "rolled_back", "rollback_incomplete"] = "recorded"
        if checkpoint_id in rolled_back:
            status = "rolled_back"
        elif checkpoint_id in rollback_started:
            status = "rollback_incomplete"
        checkpoint = FileCheckpoint(
            id=checkpoint_id, path=metadata["path"], created_at=metadata["created_at"],
            created_file=metadata["before_mode"] is None, status=status,
        )
        return _Record(checkpoint, metadata["before_mode"], metadata["after_mode"])

    def _ids(self, namespace: str) -> set[str]:
        raw = self._git("for-each-ref", "--format=%(refname:lstrip=3)", namespace).decode("ascii")
        return {line for line in raw.splitlines() if re.fullmatch(r"[0-9a-f]{32}", line)}

    def list(self) -> list[FileCheckpoint]:
        self._validate_storage()
        if not self.repository.is_dir():
            return []
        with self._locked():
            rolled_back = self._ids(_ROLLED_BACK)
            rollback_started = self._ids(_ROLLBACK_STARTED)
            records = [
                self._record(checkpoint_id, rolled_back, rollback_started).checkpoint
                for checkpoint_id in self._ids(_CHECKPOINTS)
            ]
            return sorted(records, key=lambda checkpoint: (checkpoint.created_at, checkpoint.id), reverse=True)

    def _lookup(self, checkpoint_id: str) -> _Record:
        self._validate_id(checkpoint_id)
        if not self.repository.is_dir() or checkpoint_id not in self._ids(_CHECKPOINTS):
            raise FileCheckpointError(f"Unknown file checkpoint: {checkpoint_id}")
        return self._record(checkpoint_id, self._ids(_ROLLED_BACK), self._ids(_ROLLBACK_STARTED))

    def _states(self, record: _Record) -> tuple[_FileState | None, _FileState]:
        ref = f"{_CHECKPOINTS}/{record.checkpoint.id}"
        before = None
        if record.before_mode is not None:
            before = _FileState(self._git("cat-file", "blob", f"{ref}:before"), record.before_mode)
        after = _FileState(self._git("cat-file", "blob", f"{ref}:after"), record.after_mode)
        return before, after

    def diff(self, checkpoint_id: str) -> str:
        self._validate_storage()
        if not self.repository.is_dir():
            raise FileCheckpointError("No file checkpoints have been recorded.")
        with self._locked():
            record = self._lookup(checkpoint_id)
            before, after = self._states(record)
            with TemporaryDirectory() as directory:
                scratch = Path(directory)
                old_path = os.devnull
                if before is not None:
                    (scratch / "before").write_bytes(before.data)
                    old_path = "before"
                (scratch / "after").write_bytes(after.data)
                patch = self._run_git(
                    ["diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--", old_path, "after"],
                    cwd=scratch, accepted=(0, 1),
                )
        action = "Created" if record.checkpoint.created_file else "Modified"
        return f"{action} {record.checkpoint.path}\n" + patch.decode("utf-8", errors="replace")

    def rollback(self, checkpoint_id: str) -> bool:
        """Consume a checkpoint and restore its prior state when the target matches."""
        self._validate_storage()
        if not self.repository.is_dir():
            raise FileCheckpointError("No file checkpoints have been recorded.")
        with self._locked():
            record = self._lookup(checkpoint_id)
            if record.checkpoint.rolled_back:
                return False
            if record.checkpoint.status == "rollback_incomplete":
                raise FileCheckpointError("A previous rollback was interrupted. Inspect the file and checkpoint diff.")
            self._require_worktree()
            target = self._target(self.workdir / record.checkpoint.path)
            before, after = self._states(record)
            self._expect(target, after)
            if before is None:
                self._restore(record, target, after, None)
            else:
                with TemporaryDirectory(dir=target.parent, prefix=".minibot-file-") as directory:
                    staged = Path(directory) / "file"
                    self._stage(staged, before.data, before.mode)
                    self._restore(record, target, after, staged)
            return True

    def _restore(self, record: _Record, target: Path, after: _FileState, staged: Path | None) -> None:
        checkpoint_id = record.checkpoint.id
        started = f"{_ROLLBACK_STARTED}/{checkpoint_id}"
        completed = f"{_ROLLED_BACK}/{checkpoint_id}"
        tree = self._git("rev-parse", f"{_CHECKPOINTS}/{checkpoint_id}").decode("ascii").strip()
        # An interruption across the filesystem update must not allow a replay.
        self._git("update-ref", started, tree)
        try:
            self._expect(target, after)
            if staged is None:
                target.unlink()
            else:
                staged.replace(target)
        except Exception:
            with suppress(FileCheckpointError):
                self._git("update-ref", "-d", started)
            raise
        update = f"create {completed} {tree}\ndelete {started} {tree}\n"
        self._git("update-ref", "--stdin", data=update.encode("ascii"))
