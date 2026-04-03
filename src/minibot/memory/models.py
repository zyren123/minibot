"""Structured memory domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryNamespace:
    """One isolated memory space, typically representing a novel."""

    id: str
    slug: str
    title: str
    description: str | None = None


@dataclass
class MemoryNode:
    """A memory tree node."""

    id: str
    namespace_id: str
    parent_id: str | None
    slug: str
    uri: str
    title: str
    kind: str
    node_type: str | None
    content: str
    is_core: bool
    priority: int
