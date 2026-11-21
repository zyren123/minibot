"""Filesystem and command operations bound to one execution workspace."""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Literal, Protocol

from ...config.schema import BashConfig, Config
from ...core.workspace import WorkspaceIdentity, configured_workspace_identity, trusted_workspace_identity
from ...file_checkpoints import GitFileCheckpoints, finish_file_operation
from ...utils.path import safe_path
from .bash_execution import DockerCommand, DockerLease, ExecutionResult, LocalCommand
from .workspace_files import (
    MAX_MUTATION_BYTES, OUTPUT_LIMIT_EXIT, FileOutputLimit, bounded_file_text, bounded_listing, restricted_parts,
)


_RESTRICTED_HELPER = Path(__file__).with_name("workspace_files.py").read_text(encoding="utf-8")
_DEFAULT_CAPTURE_BYTES = 8 * 1024 * 1024
_LIST_CAPTURE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WorkspacePath:
    """Canonical workspace-relative name and, in trusted local mode, its resolved host path."""

    relative: str
    host_path: Path | None = None


class WorkspaceBackend(Protocol):
    """An execution workspace; cancelling an async operation propagates to its supervisor."""

    @property
    def identity(self) -> WorkspaceIdentity: ...

    def target(self, path: str) -> WorkspacePath: ...

    async def run(
        self, command: str, *, timeout: float, output_limit: int | None = None,
    ) -> ExecutionResult: ...

    async def read_file(self, path: str, *, limit: int | None, max_bytes: int | None) -> str: ...

    async def write_file(self, target: WorkspacePath, content: str) -> None: ...

    async def edit_file(self, target: WorkspacePath, old_text: str, new_text: str) -> bool: ...

    async def list_dir(self, path: str) -> list[str]: ...


class LocalBackend:
    """Run trusted Python file operations and the configured legacy Bash policy."""

    def __init__(
        self, workdir: Path, *, bash: BashConfig | None = None, checkpoints: GitFileCheckpoints | None = None,
    ) -> None:
        self.workdir = workdir.resolve()
        self.checkpoints = checkpoints
        policy = bash or BashConfig()
        self.identity = trusted_workspace_identity(self.workdir, policy)
        self._command = DockerCommand(self.workdir, policy) if policy.backend == "docker" else LocalCommand(self.workdir)

    def _path(self, path: str) -> Path:
        return safe_path(self.workdir, path)

    def target(self, path: str) -> WorkspacePath:
        resolved = self._path(path)
        return WorkspacePath(resolved.relative_to(self.workdir).as_posix(), resolved)

    async def run(
        self, command: str, *, timeout: float, output_limit: int | None = None,
    ) -> ExecutionResult:
        return await self._command.run(command, timeout=timeout, output_limit=output_limit)

    async def read_file(self, path: str, *, limit: int | None, max_bytes: int | None) -> str:
        file_path = self._path(path)

        def read() -> str:
            if max_bytes is None:
                lines = file_path.read_text(encoding="utf-8").splitlines()
                return "\n".join(lines[:limit] if limit else lines)
            with file_path.open("rb") as file:
                return bounded_file_text(file, limit, max_bytes)

        return await asyncio.to_thread(read)

    async def write_file(self, target: WorkspacePath, content: str) -> None:
        file_path = target.host_path
        assert file_path is not None
        if self.checkpoints is not None:
            await finish_file_operation(lambda: self.checkpoints.write(file_path, content))
            return

        def write() -> None:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(write)

    async def edit_file(self, target: WorkspacePath, old_text: str, new_text: str) -> bool:
        file_path = target.host_path
        assert file_path is not None
        if self.checkpoints is not None:
            return await finish_file_operation(lambda: self.checkpoints.edit(file_path, old_text, new_text))

        def edit() -> bool:
            content = file_path.read_text(encoding="utf-8")
            if old_text not in content:
                return False
            file_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return True

        return await asyncio.to_thread(edit)

    async def list_dir(self, path: str) -> list[str]:
        file_path = self._path(path)

        def list_names() -> list[str]:
            with os.scandir(file_path) as entries:
                return bounded_listing(entries)

        return await asyncio.to_thread(list_names)


class DockerBackend:
    """Run each operation in a fresh container with the same read-write bind mount."""

    def __init__(self, config: Config, *, lease: DockerLease | None = None) -> None:
        self.timeout = config.workspace.timeout or config.tools.timeout
        self._command = DockerCommand(config.workdir.resolve(), config.workspace, lease)
        self.identity = configured_workspace_identity(config)
        self.validate_root()

    def validate_root(self) -> None:
        self._command.bind_root()

    async def reconcile(self) -> None:
        await self._command.reconcile()

    def cleanup_pending(self) -> bool:
        lease = self._command.lease
        return lease is not None and lease.pending() is not None

    def target(self, path: str) -> WorkspacePath:
        return WorkspacePath("/".join(restricted_parts(path)) or ".")

    async def run(
        self, command: str, *, timeout: float, output_limit: int | None = None,
    ) -> ExecutionResult:
        return await self._command.run(command, timeout=timeout, output_limit=output_limit)

    async def _file_call(
        self, operation: Literal["read", "write", "edit", "list"], path: str, output_limit: int, **arguments: Any,
    ) -> str:
        target = self.target(path)
        if operation != "list" and target.relative == ".":
            raise ValueError("Path must name a file.")
        payload = json.dumps(
            {"operation": operation, "path": target.relative, **arguments}, ensure_ascii=True,
        ).encode("utf-8")
        result = await self._command.execute(
            "python3", "-I", "-B", "-c", _RESTRICTED_HELPER,
            stdin=payload, timeout=self.timeout, output_limit=max(output_limit, 4096),
        )
        if result.exit_code == OUTPUT_LIMIT_EXIT:
            raise FileOutputLimit("File output exceeded the capture limit.")
        if result.incomplete_output or result.output_exceeded:
            raise RuntimeError("Docker file output collection was incomplete.")
        if result.exit_code:
            raise RuntimeError(result.stderr.strip() or f"Docker file operation exited with code {result.exit_code}.")
        return result.stdout

    async def read_file(self, path: str, *, limit: int | None, max_bytes: int | None) -> str:
        capture = max_bytes if max_bytes is not None else _DEFAULT_CAPTURE_BYTES
        text = await self._file_call("read", path, capture, limit=limit, max_bytes=capture)
        return "\n".join(text.splitlines()) if max_bytes is None else text

    async def write_file(self, target: WorkspacePath, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_MUTATION_BYTES:
            raise ValueError("File content exceeds the 16 MiB mutation limit.")
        await self._file_call("write", target.relative, 4096, content=content)

    async def edit_file(self, target: WorkspacePath, old_text: str, new_text: str) -> bool:
        if any(len(text.encode("utf-8")) > MAX_MUTATION_BYTES for text in (old_text, new_text)):
            raise ValueError("File content exceeds the 16 MiB mutation limit.")
        return (await self._file_call("edit", target.relative, 4096, old_text=old_text, new_text=new_text)) == "true"

    async def list_dir(self, path: str) -> list[str]:
        return json.loads(await self._file_call("list", path, _LIST_CAPTURE_BYTES))
