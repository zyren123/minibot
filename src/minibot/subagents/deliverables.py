"""Export child workspace files and verify them before explicit integration."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING
from uuid import uuid4

from ..core.artifacts import ArtifactError, ArtifactStore
from ..core.run_store import ChildRun
from ..tools.builtin.workspace_files import restricted_parts

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
