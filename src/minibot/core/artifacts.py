"""Session-scoped storage for complete text tool results."""

from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from heapq import nlargest
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from typing import BinaryIO, Literal
from uuid import uuid4

from ..config.schema import MAX_ARTIFACT_BYTES


_ID = re.compile(r"[A-Za-z0-9_-]{21}[AQgw]")
_HEADER_LIMIT = 4096
_CATALOG_SIZE = 8
MAX_READ_CHARS = 4096
MAX_QUERY_CHARS = 256


class ArtifactError(RuntimeError):
    """An artifact operation failed without exposing a storage path."""


class InvalidArtifactRequest(ArtifactError):
    """A retrieval request has invalid arguments."""


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str
    run_id: str
    tool_name: str
    created_at: str
    size_bytes: int
    content_type: str
    location: str
    workspace_id: str | None = None


@dataclass(frozen=True)
class ArtifactMatch:
    offset: int
    snippet_start: int
    snippet: str


def _private(info: os.stat_result) -> bool:
    return os.name != "posix" or (info.st_uid == os.getuid() and not info.st_mode & 0o077)


class ArtifactStore:
    """Publish one bounded record per ID beneath an application-owned directory."""

    def __init__(
        self, app_home: Path, workdir: Path, sessions_dir: Path, session_id: str, max_bytes: int,
        *, workspace_id: str | None = None,
    ) -> None:
        self.root = app_home.resolve() / "artifacts"
        identity = b"\0".join([
            os.fsencode(workdir.resolve()), os.fsencode(sessions_dir.resolve()), session_id.encode("utf-8"),
        ])
        self.workspace_id = workspace_id
        if workspace_id is not None:
            identity += b"\0" + workspace_id.encode("ascii")
        self.directory = self.root / sha256(identity).hexdigest()
        self.max_bytes = max_bytes

    def _check_directories(self, *, create: bool = False) -> None:
        for path in (self.root, self.directory):
            if create:
                path.mkdir(mode=0o700, parents=path == self.root, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or not _private(info) or path.resolve() != path:
                raise ArtifactError("Artifact storage is not private.")

    def save(self, text: str, run_id: str, tool_name: str) -> ArtifactMetadata:
        if len(text) > self.max_bytes:
            raise ArtifactError(f"Output exceeds the {self.max_bytes}-byte artifact limit.")
        try:
            content = text.encode("utf-8")
        except UnicodeError as exc:
            raise ArtifactError("Output is not valid UTF-8 text.") from exc
        if len(content) > self.max_bytes:
            raise ArtifactError(f"Output exceeds the {self.max_bytes}-byte artifact limit.")

        artifact_id = base64.urlsafe_b64encode(uuid4().bytes).decode("ascii").rstrip("=")
        metadata = ArtifactMetadata(
            artifact_id, run_id, tool_name, datetime.now(timezone.utc).isoformat(),
            len(content), "text/plain; charset=utf-8", f"{artifact_id}.artifact", self.workspace_id,
        )
        fields = asdict(metadata)
        if metadata.workspace_id is None:
            del fields["workspace_id"]
        header = json.dumps(fields, ensure_ascii=True).encode("ascii") + b"\n"
        if len(header) > _HEADER_LIMIT:
            raise ArtifactError("Artifact metadata exceeds its storage limit.")
        temporary = None
        try:
            self._check_directories(create=True)
            with NamedTemporaryFile("wb", prefix=".pending-", dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                file.write(header)
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.directory / metadata.location)
        except OSError as exc:
            raise ArtifactError("Artifact storage unavailable.") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return metadata

    @staticmethod
    def _open_record(path: str, flags: int) -> int:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or not _private(info):
            raise ArtifactError("Artifact unavailable.")
        descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not _private(opened) or not os.path.samestat(info, opened):
                raise ArtifactError("Artifact unavailable.")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _metadata(file: BinaryIO, artifact_id: str) -> ArtifactMetadata:
        header = file.readline(_HEADER_LIMIT + 1)
        if len(header) > _HEADER_LIMIT or not header.endswith(b"\n"):
            raise ArtifactError("Invalid artifact record.")
        metadata = ArtifactMetadata(**json.loads(header))
        if (
            metadata.artifact_id != artifact_id or metadata.location != f"{artifact_id}.artifact"
            or type(metadata.size_bytes) is not int or metadata.size_bytes < 0
            or not isinstance(metadata.tool_name, str)
        ):
            raise ArtifactError("Invalid artifact record.")
        return metadata

    def _load(self, artifact_id: str, *, include_content: bool) -> tuple[ArtifactMetadata, str]:
        if not isinstance(artifact_id, str) or _ID.fullmatch(artifact_id) is None:
            raise InvalidArtifactRequest("Invalid artifact ID.")
        try:
            self._check_directories()
            path = self.directory / f"{artifact_id}.artifact"
            with open(path, "rb", opener=self._open_record) as file:
                metadata = self._metadata(file, artifact_id)
                if metadata.workspace_id != self.workspace_id:
                    raise ArtifactError("Artifact unavailable in this workspace.")
                if not include_content:
                    return metadata, ""
                if metadata.size_bytes > self.max_bytes:
                    raise ArtifactError("Artifact exceeds the configured size limit.")
                data = file.read(metadata.size_bytes + 1)
                if len(data) != metadata.size_bytes:
                    raise ArtifactError("Invalid artifact record.")
                return metadata, data.decode("utf-8")
        except (FileNotFoundError, PermissionError) as exc:
            raise ArtifactError("Artifact unavailable in this session.") from exc
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ArtifactError("Invalid or unavailable artifact record.") from exc

    def read(self, artifact_id: str, offset: int, limit: int) -> tuple[ArtifactMetadata, str]:
        if type(offset) is not int or offset < 0:
            raise InvalidArtifactRequest("Offset must be a nonnegative integer.")
        if type(limit) is not int or not 1 <= limit <= MAX_READ_CHARS:
            raise InvalidArtifactRequest(f"Limit must be between 1 and {MAX_READ_CHARS}.")
        metadata, text = self._load(artifact_id, include_content=True)
        if offset > len(text):
            raise InvalidArtifactRequest("Offset is past the end of the artifact.")
        return metadata, text[offset:offset + limit]

    def search(self, artifact_id: str, query: str) -> tuple[ArtifactMetadata, list[ArtifactMatch]]:
        if not isinstance(query, str) or not 1 <= len(query) <= MAX_QUERY_CHARS:
            raise InvalidArtifactRequest(f"Query must contain 1 to {MAX_QUERY_CHARS} characters.")
        metadata, text = self._load(artifact_id, include_content=True)
        matches = []
        start = 0
        while len(matches) < 20:
            offset = text.find(query, start)
            if offset == -1:
                break
            snippet_start = max(0, offset - 32)
            snippet = text[snippet_start:offset + len(query) + 32]
            matches.append(ArtifactMatch(offset, snippet_start, snippet))
            start = offset + 1
        return metadata, matches

    def recent(self) -> list[ArtifactMetadata]:
        """Find a bounded set of recently published records without loading text."""
        try:
            self._check_directories()
            with os.scandir(self.directory) as entries:
                records = (
                    (item.stat(follow_symlinks=False).st_mtime_ns, item.name.removesuffix(".artifact"))
                    for item in entries if item.name.endswith(".artifact") and item.is_file(follow_symlinks=False)
                )
                latest = nlargest(_CATALOG_SIZE, records)
        except (OSError, ArtifactError):
            return []
        result = []
        for _, artifact_id in latest:
            try:
                metadata, _ = self._load(artifact_id, include_content=False)
            except ArtifactError:
                continue
            result.append(metadata)
        return result


ArtifactEvent = Literal["artifact_created", "artifact_read", "artifact_search"]
ArtifactObserver = Callable[[ArtifactEvent, ArtifactMetadata], None]


@dataclass(frozen=True)
class ArtifactRun:
    store: ArtifactStore
    run_id: str
    threshold_chars: int
    output_chars: int
    observe: ArtifactObserver | None
    catalog: list[ArtifactMetadata] = field(default_factory=list)

    def record(self, event: ArtifactEvent, metadata: ArtifactMetadata) -> None:
        if event == "artifact_created":
            self.catalog.insert(0, metadata)
            del self.catalog[_CATALOG_SIZE:]
        if self.observe is not None:
            self.observe(event, metadata)

    @property
    def capture_bytes(self) -> int:
        max_inline_bytes = min(self.threshold_chars * 4, MAX_ARTIFACT_BYTES)
        return max(self.store.max_bytes, max_inline_bytes)

    def failure(self, detail: str) -> str:
        return f"Tool ran. Output not saved. {detail}"[:self.output_chars]

    def context(self) -> str:
        if not self.catalog:
            return ""
        names = [re.sub(r"[^A-Za-z0-9_-]", "?", item.tool_name[:64]) for item in self.catalog]
        listing = "\n".join(
            f"{item.artifact_id} ({name}, {item.size_bytes} bytes)"
            for item, name in zip(self.catalog, names, strict=True)
        )
        return (
            "\n\nRecent session artifacts (outputs stored separately). "
            "Use read_artifact or search_artifact when permitted.\n" + listing
        )


current_artifacts: ContextVar[ArtifactRun | None] = ContextVar("context_artifacts", default=None)
