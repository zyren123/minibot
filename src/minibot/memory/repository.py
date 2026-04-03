"""Structured memory repository."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import uuid
from typing import Any

from sqlalchemy import case, func
from sqlmodel import Session, select

from ..config.schema import MemoryConfig
from .db import MemoryDatabase, create_memory_db
from .db_models import (
    MemoryNamespaceRow,
    MemoryNodeRow,
    MemoryNodeVersionRow,
    MemorySearchDocumentRow,
    MemoryTriggerRow,
)
from .models import MemoryNamespace, MemoryNode


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _normalize_term(term: str) -> str:
    return " ".join(term.strip().lower().split())


class MemoryRepository:
    """Small repository over SQLite/PostgreSQL for structured memory."""

    def __init__(self, *, backend: str, database: MemoryDatabase) -> None:
        self.backend = backend
        self.database = database
        self.database_path = database.database_path

    @classmethod
    def from_config(cls, config: MemoryConfig, *, app_home: Path) -> "MemoryRepository":
        backend = (config.backend or "sqlite").strip().lower()
        database = create_memory_db(config, app_home=app_home)
        return cls(backend=backend, database=database)

    def create_namespace(self, *, slug: str, title: str, description: str | None = None) -> MemoryNamespace:
        with self.database.session_factory() as session:
            existing = self._get_namespace_row_by_slug(session, slug)
            if existing is not None:
                raise ValueError(f"Namespace already exists: {slug}")
            now = _utc_now()
            row = MemoryNamespaceRow(
                id=_new_id("ns"),
                slug=slug,
                title=title,
                description=description,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return self._row_to_namespace(row)

    def list_namespaces(self) -> list[MemoryNamespace]:
        with self.database.session_factory() as session:
            rows = session.exec(
                select(MemoryNamespaceRow).order_by(MemoryNamespaceRow.slug)
            ).all()
            return [self._row_to_namespace(row) for row in rows]

    def get_namespace_by_slug(self, slug: str) -> MemoryNamespace | None:
        with self.database.session_factory() as session:
            row = self._get_namespace_row_by_slug(session, slug)
            if row is None:
                return None
            return self._row_to_namespace(row)

    def create_node(
        self,
        namespace_slug: str,
        *,
        parent_uri: str | None,
        slug: str,
        title: str,
        kind: str,
        node_type: str | None = None,
        content: str = "",
        is_core: bool = False,
        priority: int = 0,
    ) -> MemoryNode:
        with self.database.session_factory() as session:
            namespace = self._require_namespace_row(session, namespace_slug)
            parent = self._require_parent(session, namespace.id, parent_uri)
            uri = self._compose_uri(parent.uri if parent else None, slug)
            self._ensure_uri_available(session, namespace.id, uri)
            now = _utc_now()
            row = MemoryNodeRow(
                id=_new_id("node"),
                namespace_id=namespace.id,
                parent_id=parent.id if parent else None,
                slug=slug,
                uri=uri,
                title=title,
                kind=kind,
                node_type=node_type,
                content=content,
                is_core=is_core,
                priority=priority,
                created_at=now,
                updated_at=now,
                last_accessed_at=None,
                deleted_at=None,
            )
            session.add(row)
            session.flush()
            node = self._row_to_node(row)
            self._upsert_search_document(session, node)
            self._record_version(session, node, operation="create", change={"created": True})
            session.commit()
            return node

    def update_node(self, namespace_slug: str, uri: str, **fields: Any) -> MemoryNode:
        with self.database.session_factory() as session:
            node_row = self._require_node_row(session, namespace_slug, uri)
            parent_id = node_row.parent_id
            new_slug = node_row.slug
            if "slug" in fields and fields["slug"]:
                new_slug = str(fields["slug"])
            if "parent_uri" in fields:
                parent = self._require_parent(session, node_row.namespace_id, fields["parent_uri"])
                parent_id = parent.id if parent else None
                parent_uri = parent.uri if parent else None
            else:
                parent_uri = self._parent_uri_by_id(node_row.parent_id, session=session)
            new_uri = self._compose_uri(parent_uri, new_slug)
            if new_uri != node_row.uri:
                self._ensure_uri_available(
                    session,
                    node_row.namespace_id,
                    new_uri,
                    exclude_node_id=node_row.id,
                )
            node_row.parent_id = parent_id
            node_row.slug = new_slug
            node_row.uri = new_uri
            node_row.title = fields.get("title", node_row.title)
            node_row.node_type = fields.get("node_type", node_row.node_type)
            node_row.content = fields.get("content", node_row.content)
            node_row.is_core = bool(fields.get("is_core", node_row.is_core))
            node_row.priority = int(fields.get("priority", node_row.priority))
            node_row.updated_at = _utc_now()
            session.add(node_row)
            session.flush()
            if new_uri != uri:
                self._update_descendant_uris(session, node_row.namespace_id, node_row.id, uri, new_uri)
            node = self._row_to_node(node_row)
            self._upsert_search_document(session, node)
            self._record_version(session, node, operation="update", change=fields)
            session.commit()
            return node

    def delete_node(self, namespace_slug: str, uri: str) -> None:
        with self.database.session_factory() as session:
            node_row = self._require_node_row(session, namespace_slug, uri)
            child = session.exec(
                select(MemoryNodeRow.id).where(
                    MemoryNodeRow.namespace_id == node_row.namespace_id,
                    MemoryNodeRow.parent_id == node_row.id,
                    MemoryNodeRow.deleted_at.is_(None),
                ).limit(1)
            ).first()
            if child is not None:
                raise ValueError("Cannot delete node with children")
            now = _utc_now()
            node_row.deleted_at = now
            node_row.updated_at = now
            session.add(node_row)
            self._delete_search_document(session, node_row.id)
            node = self._row_to_node(node_row)
            self._record_version(session, node, operation="delete", change={"deleted": True})
            session.commit()

    def get_node_by_uri(self, namespace_slug: str, uri: str) -> MemoryNode | None:
        with self.database.session_factory() as session:
            row = self._find_node_row(session, namespace_slug, uri)
            if row is None:
                return None
            return self._row_to_node(row)

    def list_children(self, namespace_slug: str, parent_uri: str | None = None) -> list[MemoryNode]:
        with self.database.session_factory() as session:
            namespace = self._require_namespace_row(session, namespace_slug)
            statement = select(MemoryNodeRow).where(
                MemoryNodeRow.namespace_id == namespace.id,
                MemoryNodeRow.deleted_at.is_(None),
            )
            if parent_uri is None:
                statement = statement.where(MemoryNodeRow.parent_id.is_(None))
            else:
                parent = self._require_node_row(session, namespace_slug, parent_uri)
                statement = statement.where(MemoryNodeRow.parent_id == parent.id)
            rows = session.exec(
                statement.order_by(MemoryNodeRow.kind, MemoryNodeRow.priority.desc(), MemoryNodeRow.title)
            ).all()
            return [self._row_to_node(row) for row in rows]

    def list_core_nodes(self, namespace_slug: str) -> list[MemoryNode]:
        with self.database.session_factory() as session:
            namespace = self._require_namespace_row(session, namespace_slug)
            rows = session.exec(
                select(MemoryNodeRow)
                .where(
                    MemoryNodeRow.namespace_id == namespace.id,
                    MemoryNodeRow.is_core.is_(True),
                    MemoryNodeRow.deleted_at.is_(None),
                )
                .order_by(MemoryNodeRow.priority.desc(), MemoryNodeRow.title)
            ).all()
            return [self._row_to_node(row) for row in rows]

    def list_recent_nodes(self, namespace_slug: str, *, limit: int = 5) -> list[MemoryNode]:
        with self.database.session_factory() as session:
            namespace = self._require_namespace_row(session, namespace_slug)
            recency = case(
                (
                    (MemoryNodeRow.last_accessed_at.is_(None)) | (MemoryNodeRow.updated_at > MemoryNodeRow.last_accessed_at),
                    MemoryNodeRow.updated_at,
                ),
                else_=MemoryNodeRow.last_accessed_at,
            )
            rows = session.exec(
                select(MemoryNodeRow)
                .where(
                    MemoryNodeRow.namespace_id == namespace.id,
                    MemoryNodeRow.deleted_at.is_(None),
                )
                .order_by(recency.desc())
                .limit(limit)
            ).all()
            return [self._row_to_node(row) for row in rows]

    def list_versions(self, node_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            rows = session.exec(
                select(MemoryNodeVersionRow)
                .where(MemoryNodeVersionRow.node_id == node_id)
                .order_by(MemoryNodeVersionRow.version_no)
            ).all()
            return [
                {
                    "operation": row.operation,
                    "snapshot_uri": row.snapshot_uri,
                    "snapshot_title": row.snapshot_title,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    def list_triggers(self, namespace_slug: str, uri: str) -> list[str]:
        with self.database.session_factory() as session:
            node = self._require_node_row(session, namespace_slug, uri)
            return self._trigger_terms(session, node.id)

    def glossary_entries(self, namespace_slug: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            namespace = self._require_namespace_row(session, namespace_slug)
            rows = session.exec(
                select(MemoryTriggerRow.term, MemoryNodeRow.uri, MemoryTriggerRow.node_id)
                .join(MemoryNodeRow, MemoryNodeRow.id == MemoryTriggerRow.node_id)
                .where(
                    MemoryTriggerRow.namespace_id == namespace.id,
                    MemoryNodeRow.deleted_at.is_(None),
                )
                .order_by(MemoryTriggerRow.term)
            ).all()
            return [
                {"term": term, "uri": uri, "node_id": node_id}
                for term, uri, node_id in rows
            ]

    def update_triggers(
        self,
        namespace_slug: str,
        uri: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> list[str]:
        add = add or []
        remove = remove or []
        with self.database.session_factory() as session:
            node_row = self._require_node_row(session, namespace_slug, uri)
            namespace_id = node_row.namespace_id
            for term in add:
                normalized = _normalize_term(term)
                if not normalized:
                    continue
                existing = session.exec(
                    select(MemoryTriggerRow).where(
                        MemoryTriggerRow.namespace_id == namespace_id,
                        MemoryTriggerRow.normalized_term == normalized,
                    )
                ).first()
                if existing is not None and existing.node_id != node_row.id:
                    raise ValueError(f"Trigger already assigned in this namespace: {term}")
                if existing is None:
                    session.add(
                        MemoryTriggerRow(
                            id=_new_id("trg"),
                            namespace_id=namespace_id,
                            node_id=node_row.id,
                            term=term.strip(),
                            normalized_term=normalized,
                            created_at=_utc_now(),
                        )
                    )
            for term in remove:
                normalized = _normalize_term(term)
                rows = session.exec(
                    select(MemoryTriggerRow).where(
                        MemoryTriggerRow.namespace_id == namespace_id,
                        MemoryTriggerRow.node_id == node_row.id,
                        MemoryTriggerRow.normalized_term == normalized,
                    )
                ).all()
                for row in rows:
                    session.delete(row)
            session.flush()
            node = self._row_to_node(node_row)
            self._upsert_search_document(session, node)
            self._record_version(
                session,
                node,
                operation="trigger_update",
                change={"add": add, "remove": remove},
            )
            session.commit()
            return self._trigger_terms(session, node_row.id)

    def search_documents(
        self,
        namespace_slug: str,
        *,
        node_types: list[str] | None = None,
        include_folders: bool = False,
    ) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            namespace = self._require_namespace_row(session, namespace_slug)
            rows = session.exec(
                select(
                    MemorySearchDocumentRow.node_id,
                    MemorySearchDocumentRow.uri,
                    MemorySearchDocumentRow.title,
                    MemorySearchDocumentRow.kind,
                    MemorySearchDocumentRow.node_type,
                    MemorySearchDocumentRow.search_text,
                    MemoryNodeRow.parent_id,
                )
                .join(MemoryNodeRow, MemoryNodeRow.id == MemorySearchDocumentRow.node_id)
                .where(
                    MemorySearchDocumentRow.namespace_id == namespace.id,
                    MemoryNodeRow.deleted_at.is_(None),
                )
                .order_by(MemorySearchDocumentRow.title)
            ).all()
            filter_terms = {
                str(value).strip().lower()
                for value in (node_types or [])
                if str(value).strip()
            }
            filtered: list[dict[str, Any]] = []
            for node_id, uri, title, kind, node_type, search_text, parent_id in rows:
                if not include_folders and kind == "folder":
                    continue
                normalized_kind = kind.strip().lower()
                normalized_node_type = node_type.strip().lower() if node_type else None
                if (
                    filter_terms
                    and normalized_kind not in filter_terms
                    and normalized_node_type not in filter_terms
                ):
                    continue
                filtered.append(
                    {
                        "node_id": node_id,
                        "uri": uri,
                        "title": title,
                        "kind": kind,
                        "node_type": node_type,
                        "search_text": search_text,
                        "parent_uri": self._parent_uri_by_id(parent_id, session=session),
                        "triggers": self._trigger_terms(session, node_id),
                    }
                )
            return filtered

    def touch_node(self, namespace_slug: str, uri: str) -> None:
        with self.database.session_factory() as session:
            row = self._require_node_row(session, namespace_slug, uri)
            row.last_accessed_at = _utc_now()
            session.add(row)
            session.commit()

    def _get_namespace_row_by_slug(self, session: Session, slug: str) -> MemoryNamespaceRow | None:
        return session.exec(
            select(MemoryNamespaceRow).where(MemoryNamespaceRow.slug == slug)
        ).first()

    def _require_namespace_row(self, session: Session, slug: str) -> MemoryNamespaceRow:
        namespace = self._get_namespace_row_by_slug(session, slug)
        if namespace is None:
            raise ValueError(f"Unknown namespace: {slug}")
        return namespace

    def _require_parent(
        self,
        session: Session,
        namespace_id: str,
        parent_uri: str | None,
    ) -> MemoryNodeRow | None:
        if parent_uri is None:
            return None
        row = session.exec(
            select(MemoryNodeRow).where(
                MemoryNodeRow.namespace_id == namespace_id,
                MemoryNodeRow.uri == parent_uri,
                MemoryNodeRow.deleted_at.is_(None),
            )
        ).first()
        if row is None:
            raise ValueError(f"Unknown parent URI: {parent_uri}")
        return row

    def _find_node_row(
        self,
        session: Session,
        namespace_slug: str,
        uri: str,
    ) -> MemoryNodeRow | None:
        namespace = self._require_namespace_row(session, namespace_slug)
        return session.exec(
            select(MemoryNodeRow).where(
                MemoryNodeRow.namespace_id == namespace.id,
                MemoryNodeRow.uri == uri,
                MemoryNodeRow.deleted_at.is_(None),
            )
        ).first()

    def _require_node_row(self, session: Session, namespace_slug: str, uri: str) -> MemoryNodeRow:
        row = self._find_node_row(session, namespace_slug, uri)
        if row is None:
            raise ValueError(f"Unknown memory URI: {uri}")
        return row

    def _ensure_uri_available(
        self,
        session: Session,
        namespace_id: str,
        uri: str,
        exclude_node_id: str | None = None,
    ) -> None:
        row = session.exec(
            select(MemoryNodeRow.id).where(
                MemoryNodeRow.namespace_id == namespace_id,
                MemoryNodeRow.uri == uri,
                MemoryNodeRow.deleted_at.is_(None),
            )
        ).first()
        if row is None:
            return
        if exclude_node_id is not None and row == exclude_node_id:
            return
        raise ValueError(f"Memory URI already exists: {uri}")

    def _compose_uri(self, parent_uri: str | None, slug: str) -> str:
        slug = slug.strip().strip("/")
        if not slug:
            raise ValueError("slug must be non-empty")
        if not parent_uri:
            return f"memory://{slug}"
        return f"{parent_uri.rstrip('/')}/{slug}"

    def _parent_uri_by_id(self, parent_id: str | None, *, session: Session | None = None) -> str | None:
        if not parent_id:
            return None
        if session is not None:
            return session.exec(
                select(MemoryNodeRow.uri).where(MemoryNodeRow.id == parent_id)
            ).first()
        with self.database.session_factory() as local_session:
            return local_session.exec(
                select(MemoryNodeRow.uri).where(MemoryNodeRow.id == parent_id)
            ).first()

    def _update_descendant_uris(
        self,
        session: Session,
        namespace_id: str,
        node_id: str,
        old_uri: str,
        new_uri: str,
    ) -> None:
        rows = session.exec(
            select(MemoryNodeRow)
            .where(
                MemoryNodeRow.namespace_id == namespace_id,
                MemoryNodeRow.id != node_id,
                MemoryNodeRow.deleted_at.is_(None),
                MemoryNodeRow.uri.like(f"{old_uri}/%"),
            )
            .order_by(func.length(MemoryNodeRow.uri))
        ).all()
        now = _utc_now()
        for row in rows:
            row.uri = row.uri.replace(old_uri, new_uri, 1)
            row.updated_at = now
            session.add(row)
            self._upsert_search_document(session, self._row_to_node(row))

    def _record_version(
        self,
        session: Session,
        node: MemoryNode,
        *,
        operation: str,
        change: dict[str, Any],
    ) -> None:
        total = session.exec(
            select(func.count()).select_from(MemoryNodeVersionRow).where(MemoryNodeVersionRow.node_id == node.id)
        ).one()
        version_no = int(total) + 1
        session.add(
            MemoryNodeVersionRow(
                id=_new_id("ver"),
                node_id=node.id,
                version_no=version_no,
                operation=operation,
                snapshot_title=node.title,
                snapshot_parent_id=node.parent_id,
                snapshot_uri=node.uri,
                snapshot_kind=node.kind,
                snapshot_node_type=node.node_type,
                snapshot_content=node.content,
                snapshot_is_core=node.is_core,
                snapshot_priority=node.priority,
                snapshot_triggers_json=json.dumps(self._trigger_terms(session, node.id), ensure_ascii=False),
                change_json=json.dumps(change, ensure_ascii=False, sort_keys=True),
                created_at=_utc_now(),
            )
        )

    def _delete_search_document(self, session: Session, node_id: str) -> None:
        existing = session.exec(
            select(MemorySearchDocumentRow).where(MemorySearchDocumentRow.node_id == node_id)
        ).first()
        if existing is not None:
            session.delete(existing)

    def _upsert_search_document(self, session: Session, node: MemoryNode) -> None:
        triggers = self._trigger_terms(session, node.id)
        search_text = " ".join(
            part for part in (node.uri, node.title, node.node_type or "", node.content, " ".join(triggers)) if part
        )
        existing = session.exec(
            select(MemorySearchDocumentRow).where(MemorySearchDocumentRow.node_id == node.id)
        ).first()
        if existing is None:
            session.add(
                MemorySearchDocumentRow(
                    id=_new_id("doc"),
                    namespace_id=node.namespace_id,
                    node_id=node.id,
                    uri=node.uri,
                    title=node.title,
                    kind=node.kind,
                    node_type=node.node_type,
                    search_text=search_text,
                    updated_at=_utc_now(),
                )
            )
            return
        existing.uri = node.uri
        existing.title = node.title
        existing.kind = node.kind
        existing.node_type = node.node_type
        existing.search_text = search_text
        existing.updated_at = _utc_now()
        session.add(existing)

    def _trigger_terms(self, session: Session, node_id: str) -> list[str]:
        return session.exec(
            select(MemoryTriggerRow.term)
            .where(MemoryTriggerRow.node_id == node_id)
            .order_by(MemoryTriggerRow.term)
        ).all()

    def _row_to_namespace(self, row: MemoryNamespaceRow) -> MemoryNamespace:
        return MemoryNamespace(
            id=row.id,
            slug=row.slug,
            title=row.title,
            description=row.description,
        )

    def _row_to_node(self, row: MemoryNodeRow) -> MemoryNode:
        return MemoryNode(
            id=row.id,
            namespace_id=row.namespace_id,
            parent_id=row.parent_id,
            slug=row.slug,
            uri=row.uri,
            title=row.title,
            kind=row.kind,
            node_type=row.node_type,
            content=row.content,
            is_core=bool(row.is_core),
            priority=int(row.priority),
        )
