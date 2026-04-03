"""SQLModel table definitions for memory v2."""

from __future__ import annotations

from sqlmodel import Field, SQLModel
from sqlalchemy import Index, UniqueConstraint


class MemoryNamespaceRow(SQLModel, table=True):
    __tablename__ = "memory_namespaces"

    id: str = Field(primary_key=True)
    slug: str
    title: str
    description: str | None = None
    created_at: str
    updated_at: str

    __table_args__ = (
        UniqueConstraint("slug", name="uq_memory_namespaces_slug"),
    )


class MemoryNodeRow(SQLModel, table=True):
    __tablename__ = "memory_nodes"

    id: str = Field(primary_key=True)
    namespace_id: str = Field(index=True)
    parent_id: str | None = Field(default=None, index=True)
    slug: str
    uri: str
    title: str
    kind: str
    node_type: str | None = None
    content: str
    is_core: bool = False
    priority: int = 0
    created_at: str
    updated_at: str
    last_accessed_at: str | None = None
    deleted_at: str | None = None

    __table_args__ = (
        Index("idx_memory_nodes_namespace_uri", "namespace_id", "uri"),
        Index("idx_memory_nodes_parent", "namespace_id", "parent_id"),
    )


class MemoryNodeVersionRow(SQLModel, table=True):
    __tablename__ = "memory_node_versions"

    id: str = Field(primary_key=True)
    node_id: str = Field(index=True)
    version_no: int
    operation: str
    snapshot_title: str
    snapshot_parent_id: str | None = None
    snapshot_uri: str
    snapshot_kind: str
    snapshot_node_type: str | None = None
    snapshot_content: str
    snapshot_is_core: bool
    snapshot_priority: int
    snapshot_triggers_json: str
    change_json: str | None = None
    created_at: str


class MemoryTriggerRow(SQLModel, table=True):
    __tablename__ = "memory_triggers"

    id: str = Field(primary_key=True)
    namespace_id: str = Field(index=True)
    node_id: str = Field(index=True)
    term: str
    normalized_term: str
    created_at: str

    __table_args__ = (
        UniqueConstraint("namespace_id", "normalized_term", name="uq_memory_triggers_namespace_term"),
    )


class MemorySearchDocumentRow(SQLModel, table=True):
    __tablename__ = "memory_search_documents"

    id: str = Field(primary_key=True)
    namespace_id: str = Field(index=True)
    node_id: str
    uri: str
    title: str
    kind: str
    node_type: str | None = None
    search_text: str
    updated_at: str

    __table_args__ = (
        UniqueConstraint("node_id", name="uq_memory_search_documents_node"),
    )
