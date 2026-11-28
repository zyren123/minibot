"""File and Novel Memo memory surfaces shared by the agent and WebUI."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import stat
from _thread import RLock
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Literal, TypedDict

import yaml

from ..config.schema import MemoryConfig
from .manager import MemoryManager

if TYPE_CHECKING:
    from novelmemo.config import Config as NovelMemoConfig
    from novelmemo.storage.markdown.store import MarkdownVaultStore
    from novelmemo.storage.markdown.vault_layout import VaultLayout

_LONG_TERM = "LONG_TERM.md"
_DAILY = "daily"
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DOMAIN_PATTERN = re.compile(r"[a-z_][a-z_0-9]*")
_REGISTRY_LOCK = RLock()
_FILE_LOCKS: dict[Path, RLock] = {}
_VAULTS: dict[Path, NovelMemoWorkspace] = {}


class MemoryEntry(TypedDict):
    id: str
    title: str
    kind: Literal["folder", "document"]
    parent_id: str
    document_id: str | None


class MemoryBreadcrumb(TypedDict):
    id: str
    title: str


class MemoryDirectory(TypedDict):
    breadcrumbs: list[MemoryBreadcrumb]
    entries: list[MemoryEntry]


class MemoryDocument(TypedDict):
    id: str
    title: str
    filename: str
    content: str
    revision: str
    triggers: list[str]
    disclosure: str | None


class MemoryRevisionConflict(ValueError):
    """The document changed after the caller opened it."""


def _path_parts(value: str) -> list[str]:
    if not value:
        return []
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(
        character in "\\:" or ord(character) < 32 for character in value
    ):
        raise ValueError("Invalid memory path")
    return parts


def _safe_path(root: Path, path: str) -> Path:
    target = root
    for segment in _path_parts(path):
        target = target / segment
        if target.is_symlink():
            raise ValueError("Symbolic links are unavailable in memory paths")
    return target


def _check_storage_tree(root: Path, path: str) -> None:
    base = _safe_path(root, path)

    def raise_walk_error(error: OSError) -> None:
        raise error

    for folder, dirs, files in os.walk(base, onerror=raise_walk_error):
        for name in dirs + files:
            if (Path(folder) / name).is_symlink():
                raise ValueError("Symbolic links are unavailable in Novel Memo storage")


def _revision(path: Path, data: bytes | None) -> str:
    checksum = hashlib.sha256(os.fsencode(path))
    checksum.update(b"\0\1" if data is not None else b"\0\0")
    if data is not None:
        checksum.update(data)
    return checksum.hexdigest()


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _document_entry(path: str, title: str) -> MemoryEntry:
    return MemoryEntry(
        id=path, title=title, kind="document", parent_id=path.rpartition("/")[0],
        document_id=path,
    )


def _folder_entry(path: str, title: str, document: str | None = None) -> MemoryEntry:
    return MemoryEntry(
        id=path, title=title, kind="folder", parent_id=path.rpartition("/")[0],
        document_id=document,
    )


class FileMemoryWorkspace:
    def __init__(self, config: MemoryConfig, app_home: Path, project_root: Path):
        memory_dir = (app_home / Path(config.memory_dir).expanduser()).resolve()
        with _REGISTRY_LOCK:
            self._lock = _FILE_LOCKS.setdefault(memory_dir, RLock())
        with self._lock:
            _safe_path(memory_dir, _LONG_TERM)
            _safe_path(memory_dir, _DAILY)
            for entry in (memory_dir / _DAILY).glob("*.md"):
                if entry.is_symlink():
                    raise ValueError("Symbolic links are unavailable in memory paths")
            self.manager = MemoryManager(config, app_home, project_root)
        self.root = self.manager.memory_dir

    def _daily_entries(self) -> list[MemoryEntry]:
        dates = {date.today().isoformat()}
        for path in _safe_path(self.root, _DAILY).glob("*.md"):
            if path.is_symlink() or not path.is_file() or not _DATE_PATTERN.fullmatch(path.stem):
                continue
            try:
                date.fromisoformat(path.stem)
            except ValueError:
                continue
            dates.add(path.stem)
        return [_document_entry(f"daily/{day}.md", day) for day in sorted(dates, reverse=True)]

    def directory(self, path: str) -> MemoryDirectory:
        breadcrumbs: list[MemoryBreadcrumb] = [{"id": "", "title": "Memory"}]
        if path == "":
            return {
                "breadcrumbs": breadcrumbs,
                "entries": [
                    _document_entry(_LONG_TERM, "Long-term memory"),
                    _folder_entry(_DAILY, "Daily memory"),
                ],
            }
        if path != _DAILY:
            raise ValueError("Memory folder not found")
        with self._lock:
            breadcrumbs.append({"id": _DAILY, "title": "Daily memory"})
            return {"breadcrumbs": breadcrumbs, "entries": self._daily_entries()}

    def _target(self, path: str) -> tuple[Path, str]:
        if path == _LONG_TERM:
            return _safe_path(self.root, path), "Long-term memory"
        parts = _path_parts(path)
        if len(parts) != 2 or parts[0] != _DAILY or not parts[1].endswith(".md"):
            raise ValueError("Memory document not found")
        day = parts[1][:-3]
        if not _DATE_PATTERN.fullmatch(day):
            raise ValueError("Expected a date in YYYY-MM-DD format")
        date.fromisoformat(day)
        return _safe_path(self.root, path), day

    def document(self, path: str) -> MemoryDocument:
        with self._lock:
            target, title = self._target(path)
            data = _read_bytes(target)
            return MemoryDocument(
                id=path, title=title, filename=path, content=data.decode("utf-8") if data else "",
                revision=_revision(target, data), triggers=[], disclosure=None,
            )

    def save_document(
        self, path: str, content: str, revision: str, *,
        triggers: list[str] | None = None, disclosure: str | None = None,
    ) -> MemoryDocument:
        if triggers is not None or disclosure is not None:
            raise ValueError("File memory stores Markdown only")
        with self._lock:
            target, _ = self._target(path)
            if revision != _revision(target, _read_bytes(target)):
                raise MemoryRevisionConflict("Memory changed since it was opened")
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, content)
            return self.document(path)

    def edit_file(
        self, path: str, *, operation: str, text: str | None = None,
        old_text: str | None = None, new_text: str | None = None,
    ) -> str:
        with self._lock:
            current = self.document(path)
            content = current["content"]
            if operation in {"write", "overwrite"} and text is not None:
                content = text
            elif operation in {"append", "append_text"} and text is not None:
                separator = "\n" if content and not content.endswith("\n") else ""
                content += separator + text
            elif operation in {"replace", "replace_text"} and old_text and new_text is not None:
                if old_text not in content:
                    return "No matches in memory"
                content = content.replace(old_text, new_text)
            else:
                raise ValueError("Provide the text for the requested memory edit")
            self.save_document(path, content, current["revision"])
            return f"Updated {path}"

    def get_context_for_prompt(self) -> str:
        with self._lock:
            self._target(_LONG_TERM)
            today = date.today()
            for offset in range(max(0, self.manager.config.daily_lookback_days) + 1):
                self._target(f"daily/{today - timedelta(days=offset)}.md")
            return self.manager.get_context_for_prompt()

    def search(self, query: str) -> list[MemoryEntry]:
        query = query.strip().casefold()
        if not query:
            return []
        with self._lock:
            entries = [_document_entry(_LONG_TERM, "Long-term memory"), *self._daily_entries()]
            return [
                entry for entry in entries
                if query in entry["title"].casefold() or query in self.document(entry["id"])["content"].casefold()
            ][:50]

    def graph(self) -> list[MemoryEntry]:
        return []


def _compose_novelmemo(meta: dict, content: str) -> str:
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    if "---" in frontmatter:
        # Novel Memo looks for a frontmatter delimiter anywhere in the first YAML block.
        frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_style='"')
        frontmatter = frontmatter.replace("---", r"\x2D\x2D\x2D")
    body = content.rstrip("\n")
    return f"---\n{frontmatter}---\n\n{body}\n"


def _open_novelmemo_store(root: Path, config: NovelMemoConfig) -> MarkdownVaultStore:
    from novelmemo.markdown_io import apply_auto_footer
    from novelmemo.storage.markdown.related_sync import _auto_lines
    from novelmemo.storage.markdown.store import MarkdownVaultStore
    from novelmemo.uri import parse_uri

    class EmbeddedVaultStore(MarkdownVaultStore):
        def _sync_related(self, uris: set[str]) -> None:
            # Keep the upstream related-link projection without rewriting the
            # existing YAML through its unquoted 0.2.1 serializer.
            updated = set()
            for uri in sorted(uris):
                domain, name = parse_uri(uri, self.config.valid_domains)
                _path_parts(name)
                path = self._layout.resolve(domain, name)
                if path is None:
                    continue
                path = _safe_path(root, path.relative_to(root).as_posix())
                raw = path.read_text(encoding="utf-8")
                block = raw.split("---", 2) if raw.startswith("---") else []
                prefix, body = (f"---{block[1]}---", block[2]) if len(block) == 3 else ("", raw)
                full = apply_auto_footer(body, _auto_lines(self.graph_reader, uri))
                if full.rstrip("\n") != body.rstrip("\n"):
                    _atomic_write(path, prefix + full.rstrip("\n") + "\n")
                updated.add(uri)
            self._sidecar.refresh_file_fingerprints(updated)

    return EmbeddedVaultStore(root, config)


class NovelMemoWorkspace:
    def __init__(self, root: Path):
        from novelmemo.config import load_config
        from novelmemo.storage.markdown.vault_layout import VaultLayout

        self.root = root
        self._lock = RLock()
        if not _safe_path(root, ".memory/memory.yaml").is_file():
            raise ValueError("Select a Novel Memo Vault containing .memory/memory.yaml")
        _check_storage_tree(root, ".memory")
        try:
            self._config = load_config(root)
        except (AttributeError, OSError, TypeError, yaml.YAMLError) as exc:
            raise ValueError("Novel Memo Vault configuration could not be read") from exc
        if any(
            not _DOMAIN_PATTERN.fullmatch(domain) or domain == "system"
            for domain in self._config.valid_domains
        ):
            raise ValueError("Novel Memo Vault contains an invalid domain")
        for uri in self._config.boot_uris.get("", []):
            self._validate_uri(uri, allow_system=False)
        self._check_vault()
        self._layout: VaultLayout = VaultLayout(root, self._config.valid_domains)
        self._store = _open_novelmemo_store(root, self._config)

    def _check_vault(self) -> None:
        for domain in self._config.valid_domains:
            source = _safe_path(self.root, domain)
            if source.exists():
                if not source.is_dir():
                    raise ValueError("A Novel Memo domain must be a folder")
                _check_storage_tree(self.root, domain)

    def _validate_uri(self, uri: str, *, allow_system: bool = False) -> tuple[str, str]:
        from novelmemo.uri import parse_uri

        domain, name = parse_uri(uri, self._config.valid_domains)
        if domain == "system" and not allow_system:
            raise ValueError("Cannot write a Novel Memo system view")
        if domain != "system":
            _path_parts(name)
            path = f"{domain}/{name}" if name else domain
            _safe_path(self.root, path)
            if name:
                _safe_path(self.root, f"{path}.md")
        return domain, name

    def _check_index(self) -> None:
        for name in ("index.db", "index.db-shm", "index.db-wal", "index.db-journal"):
            _safe_path(self.root, f".memory/{name}")

    def _folder(self, path: Path) -> MemoryEntry:
        entry_id = path.relative_to(self.root).as_posix()
        document = None
        for name in (f"{path.name}.md", "_index.md"):
            candidate = path / name
            if not candidate.is_symlink() and candidate.is_file():
                document = candidate.relative_to(self.root).as_posix()
                break
        return _folder_entry(entry_id, path.name, document)

    def _entry_for_file(self, path: Path) -> MemoryEntry:
        if path.name == "_index.md" or self._layout.is_folder_note(path):
            return self._folder(path.parent)
        return _document_entry(path.relative_to(self.root).as_posix(), path.stem)

    def _entries(self, path: Path) -> list[MemoryEntry]:
        if path == self.root:
            return [
                self._folder(source)
                for domain in sorted(set(self._config.valid_domains))
                if (source := self.root / domain).is_dir() and not source.is_symlink()
            ]
        folders: list[MemoryEntry] = []
        documents: list[MemoryEntry] = []
        for child in sorted(path.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
            if child.is_symlink() or child.name.startswith("."):
                continue
            if child.is_dir():
                folders.append(self._folder(child))
            elif child.is_file() and child.suffix == ".md":
                entry = self._entry_for_file(child)
                if entry["kind"] == "document":
                    documents.append(entry)
        return folders + documents

    def _target(self, path: str) -> Path:
        parts = _path_parts(path)
        if parts and parts[0] not in self._config.valid_domains:
            raise ValueError("Novel Memo path is outside the configured domains")
        return _safe_path(self.root, path)

    def directory(self, path: str) -> MemoryDirectory:
        with self._lock:
            target = self._target(path)
            if not target.is_dir():
                raise ValueError("Memory folder not found")
            breadcrumbs: list[MemoryBreadcrumb] = [{"id": "", "title": self.root.name}]
            parts = _path_parts(path)
            breadcrumbs.extend(
                {"id": "/".join(parts[:index]), "title": value}
                for index, value in enumerate(parts, start=1)
            )
            return {"breadcrumbs": breadcrumbs, "entries": self._entries(target)}

    def _document_path(self, path: str) -> Path:
        target = self._target(path)
        if target.suffix != ".md" or not target.is_file():
            raise ValueError("Memory document not found")
        return target

    @staticmethod
    def _parse_document(data: bytes) -> tuple[dict, str]:
        from novelmemo.markdown_io import split_frontmatter, strip_auto_footer

        meta, content = split_frontmatter(data.decode("utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("Novel Memo frontmatter must contain a mapping")
        return meta, strip_auto_footer(content)

    @staticmethod
    def _triggers(meta: dict) -> list[str]:
        triggers = meta.get("triggers")
        if not isinstance(triggers, list):
            return []
        return [term.strip() for term in triggers if isinstance(term, str) and term.strip()]

    def document(self, path: str) -> MemoryDocument:
        with self._lock:
            target = self._document_path(path)
            data = target.read_bytes()
            meta, content = self._parse_document(data)
            return MemoryDocument(
                id=path, title=self._entry_for_file(target)["title"], filename=path,
                content=content, revision=_revision(target, data), triggers=self._triggers(meta),
                disclosure=str(meta["disclosure"]) if meta.get("disclosure") else None,
            )

    def _write_document(self, path: Path, meta: dict, content: str) -> None:
        _atomic_write(path, _compose_novelmemo(meta, content))
        self._store.reconcile_paths({path})

    @staticmethod
    def _set_disclosure(meta: dict, disclosure: str) -> None:
        if not disclosure.strip():
            raise ValueError("Novel Memo disclosure cannot be empty")
        meta["disclosure"] = disclosure.strip()

    def save_document(
        self, path: str, content: str, revision: str, *,
        triggers: list[str] | None = None, disclosure: str | None = None,
    ) -> MemoryDocument:
        with self._lock:
            target = self._document_path(path)
            self._check_index()
            self._check_vault()
            data = target.read_bytes()
            if revision != _revision(target, data):
                raise MemoryRevisionConflict("Memory changed since it was opened")
            meta, _ = self._parse_document(data)
            if disclosure is not None:
                self._set_disclosure(meta, disclosure)
            if triggers is not None:
                names = list(dict.fromkeys(term.strip() for term in triggers if term.strip()))
                if names:
                    meta["triggers"] = names
                else:
                    meta.pop("triggers", None)
            self._write_document(target, meta, content)
            return self.document(path)

    def search(self, query: str) -> list[MemoryEntry]:
        if not query.strip():
            return []
        with self._lock:
            results = self.search_memory(query, limit=50)
            entries: dict[str, MemoryEntry] = {}
            for result in results:
                try:
                    file_path = self._resolve_uri(result["uri"])
                except ValueError:
                    continue
                entry = self._entry_for_file(file_path)
                entries.setdefault(entry["id"], entry)
            return list(entries.values())

    def graph(self) -> list[MemoryEntry]:
        with self._lock:
            entries: list[MemoryEntry] = []
            folders = [self.root]
            while folders:
                for entry in self._entries(folders.pop()):
                    entries.append(entry)
                    if entry["kind"] == "folder":
                        folders.append(self._target(entry["id"]))
            return entries

    def _resolve_uri(self, uri: str) -> Path:
        self._validate_uri(uri)
        path = self._store.resolve_path(uri)
        return self._target(path.relative_to(self.root).as_posix())

    def read_memory(self, uri: str) -> str:
        with self._lock:
            self._validate_uri(uri, allow_system=True)
            self._check_index()
            self._check_vault()
            return self._store.read_memory(uri)

    def create_memory(
        self, parent_uri: str, content: str, disclosure: str, title: str | None = None,
    ) -> str:
        with self._lock:
            domain, parent = self._validate_uri(parent_uri)
            self._check_index()
            self._check_vault()
            if not disclosure.strip():
                raise ValueError("Novel Memo disclosure cannot be empty")
            if title is not None:
                from novelmemo.uri import validate_title

                validate_title(title)
                child_id = title.strip()
                _path_parts(child_id)
                parent_id = f"{domain}/{parent}" if parent else domain
                if child_id in {"_index", parent_id.rsplit("/", 1)[-1]}:
                    raise ValueError("Select a different Novel Memo title")
                child_path = self._target(f"{parent_id}/{child_id}")
                if child_path.exists() or self._target(f"{parent_id}/{child_id}.md").exists():
                    raise ValueError("Memory already exists")
            if parent:
                # Reject an occupied promotion destination before the upstream move.
                file_path = self._resolve_uri(parent_uri)
                if not self._layout.is_folder_note(file_path):
                    basename = parent.rsplit("/", 1)[-1]
                    promoted = self._target(f"{domain}/{parent}/{basename}.md")
                    if promoted.exists():
                        raise ValueError("Novel Memo folder note already exists")
            quoted_disclosure = json.dumps(disclosure.strip(), ensure_ascii=False)
            quoted_disclosure = quoted_disclosure.replace("---", r"\u002d\u002d\u002d")
            return self._store.create_memory(parent_uri, content, quoted_disclosure, title)

    def update_memory(
        self, uri: str, *, old_string: str | None = None, new_string: str | None = None,
        append: str | None = None, disclosure: str | None = None,
    ) -> str:
        if (old_string is None) != (new_string is None) or (old_string is not None and append is not None):
            raise ValueError("Use one Novel Memo edit operation at a time")
        if old_string is None and append is None and disclosure is None:
            raise ValueError("Provide a Novel Memo edit")
        with self._lock:
            target = self._resolve_uri(uri)
            self._check_index()
            self._check_vault()
            meta, content = self._parse_document(target.read_bytes())
            if old_string is not None:
                if not old_string or content.count(old_string) != 1:
                    raise ValueError("The replaced text must occur exactly once")
                content = content.replace(old_string, new_string or "", 1)
            if append is not None:
                content = content.rstrip() + append
            if disclosure is not None:
                self._set_disclosure(meta, disclosure)
            self._write_document(target, meta, content)
            return uri.strip()

    def delete_memory(self, uri: str) -> str:
        with self._lock:
            self._resolve_uri(uri)
            self._check_index()
            self._check_vault()
            return self._store.delete_memory(uri)

    def manage_triggers(
        self, uri: str, *, add: list[str] | None = None, remove: list[str] | None = None,
    ) -> list[str]:
        added = {term.strip() for term in add or [] if term.strip()}
        removed = {term.strip() for term in remove or [] if term.strip()}
        if added & removed:
            raise ValueError("A trigger cannot be added and removed in the same edit")
        with self._lock:
            target = self._resolve_uri(uri)
            self._check_index()
            self._check_vault()
            meta, content = self._parse_document(target.read_bytes())
            names = sorted((set(self._triggers(meta)) | added) - removed)
            if names:
                meta["triggers"] = names
            else:
                meta.pop("triggers", None)
            self._write_document(target, meta, content)
            return names

    def search_memory(
        self, query: str, *, domain: str | None = None, limit: int = 10,
    ) -> list[dict]:
        with self._lock:
            self._check_index()
            return self._store.search_memory(query, domain=domain, limit=limit)


def open_novelmemo_workspace(path: str | Path) -> NovelMemoWorkspace:
    selected = Path(path).expanduser()
    if not selected.is_absolute():
        raise ValueError("Select an absolute Novel Memo Vault path")
    try:
        root = selected.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Select a Novel Memo Vault folder")
        with _REGISTRY_LOCK:
            workspace = _VAULTS.get(root)
            if workspace is None:
                workspace = NovelMemoWorkspace(root)
                _VAULTS[root] = workspace
            return workspace
    except (OSError, RuntimeError, ImportError, yaml.YAMLError) as exc:
        raise ValueError("Novel Memo Vault could not be opened") from exc


async def call_memory_operation[**P, T](
    operation: Callable[P, T | Awaitable[T]], /, *args: P.args, **kwargs: P.kwargs,
) -> T:
    if inspect.iscoroutinefunction(operation):
        return await operation(*args, **kwargs)
    return await asyncio.to_thread(operation, *args, **kwargs)
