"""Export child workspace files and verify them before explicit integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, suppress
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING
from uuid import uuid4

from ..config.schema import Config
from ..config.workspace import require_outside_root
from ..core.artifacts import ArtifactError, ArtifactStore
from ..core.run_store import ChildRun, DurableRunPaused, RunJournal, RunStore
from ..tools.builtin.workspace_files import restricted_parts
from ..tools.result import ToolOutcome

if TYPE_CHECKING:
    from ..tools.builtin.workspace_backend import DockerBackend


_EXPORT = "child_deliverable"
_MAX_FILES = 32
_MAX_PATH_BYTES = 4096


@dataclass(frozen=True)
class ChildArtifact:
    path: str
    sha256: str
    artifact_id: str
    size_bytes: int


def child_outcome(
    child: ChildRun, status: str, summary: str, artifacts: tuple[ChildArtifact, ...] = (),
    delivery_error: str | None = None,
) -> ToolOutcome:
    return ToolOutcome(json.dumps({
        "child_run_id": child.run.run_id, "parent_step_id": child.parent_step_id,
        "parent_run_id": child.run.parent_run_id,
        "session_id": child.run.session_id, "workspace_id": child.run.workspace_id,
        "status": status, "summary": summary[:8000],
        "artifacts": [asdict(artifact) for artifact in artifacts],
        **({"delivery_error": delivery_error} if delivery_error is not None else {}),
    }, ensure_ascii=True), "success" if status == "completed" else "error", artifact_eligible=False)


@dataclass(frozen=True)
class ChildDeliverables:
    child: ChildRun
    store: RunStore
    config: Config

    def _artifact_store(self) -> ArtifactStore:
        run = self.child.run
        return ArtifactStore(
            self.config.app_home, self.child.workspace_path, Path(run.sessions_dir), run.session_id,
            self.config.context_offloading.max_artifact_bytes, workspace_id=run.workspace_id,
        )

    async def complete(
        self, journal: RunJournal,
        prepare_export: Callable[[], Awaitable[tuple[tuple[str, ...], DockerBackend | None]]],
    ) -> ToolOutcome:
        receipt = journal.child_delivery()
        if receipt is not None:
            status = "success" if json.loads(receipt)["status"] == "completed" else "error"
            return ToolOutcome(receipt, status, artifact_eligible=False)

        paths, backend = await prepare_export()
        artifacts: tuple[ChildArtifact, ...] = ()
        delivery_error = None
        if paths:
            assert backend is not None
            try:
                artifacts = await export_child_artifacts(self.child, backend, self._artifact_store(), paths)
            except (OSError, ValueError, RuntimeError) as exc:
                if journal.pending_container() is not None:
                    raise DurableRunPaused(
                        f"Child {self.child.run.run_id} requires Docker cleanup before delivery."
                    ) from exc
                delivery_error = str(exc)
        messages = journal.checkpoint().messages
        summary = next((msg.get("content", "") for msg in reversed(messages) if msg.get("role") == "assistant"), "")
        status = "completed" if delivery_error is None else "delivery_failed"
        outcome = child_outcome(self.child, status, summary, artifacts, delivery_error)
        journal.finish_child_delivery(outcome.text)
        return outcome

    def list_artifacts(self) -> list[ChildArtifact]:
        store = self.store.for_child(self.child)
        receipt = RunJournal(store, self.child.run.session_id, self.child.run.run_id).child_delivery()
        return [ChildArtifact(**entry) for entry in json.loads(receipt)["artifacts"]] if receipt is not None else []

    def read(self, path: str) -> str:
        _artifact_parts(path)
        for entry in self.list_artifacts():
            if entry.path == path:
                return read_child_artifact(self.child, self._artifact_store(), entry)
        raise ArtifactError("No committed child deliverable at that path.")

    def apply(self, workdir: Path, path: str, destination: str) -> bool:
        content = self.read(path)
        [relative] = parse_deliverable_paths([destination])
        workdir = workdir.resolve()
        try:
            require_outside_root(self.store.root, workdir / relative, "Deliverable destination")
        except ValueError as exc:
            raise ArtifactError("A child deliverable cannot be applied to durable run storage.") from exc
        return apply_child_artifact(workdir, relative, content)


def _artifact_parts(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or len(path.encode("utf-8")) > _MAX_PATH_BYTES:
        raise ValueError("Deliverable paths must be nonempty workspace-relative file names.")
    parts = restricted_parts(path)
    if not parts or "/".join(parts) != path:
        raise ValueError("Deliverable paths must use canonical workspace-relative file names.")
    return parts


def parse_deliverable_paths(value: object) -> tuple[str, ...]:
    """Parse up to 32 distinct canonical workspace-relative file names."""
    if not isinstance(value, list) or len(value) > _MAX_FILES:
        raise ValueError(f"Deliverables must be an array of up to {_MAX_FILES} file paths.")
    paths = tuple("/".join(_artifact_parts(path)) for path in value)
    if len(set(paths)) != len(paths):
        raise ValueError("Deliverable paths must be unique.")
    return paths


async def export_child_artifacts(
    child: ChildRun, backend: DockerBackend, store: ArtifactStore, paths: tuple[str, ...],
) -> tuple[ChildArtifact, ...]:
    """Read exactly the requested files through the child's restricted backend."""
    workspace_id = child.run.workspace_id
    if backend.identity.value != workspace_id or store.workspace_id != workspace_id:
        raise ArtifactError("Child deliverables require the recorded workspace.")
    exported = []
    for path in paths:
        text = await backend.read_file(path, limit=None, max_bytes=store.max_bytes)
        metadata = await asyncio.to_thread(store.save, text, child.run.run_id, _EXPORT, key=path)
        exported.append(ChildArtifact(
            path, sha256(text.encode("utf-8")).hexdigest(), metadata.artifact_id, metadata.size_bytes,
        ))
    return tuple(exported)


def read_child_artifact(child: ChildRun, store: ArtifactStore, entry: ChildArtifact) -> str:
    """Read and verify the complete UTF-8 payload listed in a child's manifest."""
    metadata, text = store.read_all(entry.artifact_id, key=entry.path)
    content = text.encode("utf-8")
    if (
        metadata.run_id != child.run.run_id or metadata.workspace_id != child.run.workspace_id
        or metadata.tool_name != _EXPORT or len(content) != entry.size_bytes
        or sha256(content).hexdigest() != entry.sha256
    ):
        raise ArtifactError("Child deliverable does not match its recorded manifest.")
    return text


def _check_existing(parent: int, name: str, content: bytes) -> None:
    expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(expected.st_mode) or expected.st_size != len(content):
        raise ArtifactError("Destination already exists with different contents.")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY
    descriptor = os.open(name, flags, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as file:
        info = os.fstat(file.fileno())
        if not os.path.samestat(info, expected) or file.read(len(content) + 1) != content:
            raise ArtifactError("Destination already exists with different contents.")


def apply_child_artifact(workdir: Path, destination: str, content: str) -> bool:
    """Create an explicit file in an existing directory. Identical contents are a no-op."""
    parts = _artifact_parts(destination)
    if os.name != "posix":
        raise ArtifactError("Applying child deliverables requires POSIX workspace file operations.")
    data = content.encode("utf-8")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with ExitStack() as resources:
            parent = os.open(workdir, directory_flags)
            resources.callback(os.close, parent)
            for part in parts[:-1]:
                parent = os.open(part, directory_flags, dir_fd=parent)
                resources.callback(os.close, parent)
            name = parts[-1]
            try:
                _check_existing(parent, name, data)
            except FileNotFoundError:
                pass
            else:
                return False
            temporary = f".minibot-deliverable-{uuid4().hex}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                except FileExistsError:
                    _check_existing(parent, name, data)
                    return False
                return True
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=parent)
    except OSError as exc:
        raise ArtifactError("Could not apply the child deliverable at the specified destination.") from exc
