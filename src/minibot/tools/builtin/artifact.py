"""Bounded model retrieval for artifacts from the current session."""

import asyncio
import json
from typing import Any

from ...core.artifacts import ArtifactError, InvalidArtifactRequest, MAX_QUERY_CHARS, MAX_READ_CHARS, current_artifacts
from ..base import ResultTool
from ..result import ToolOutcome


_ARTIFACT_ID = {"type": "string", "description": "Opaque ID returned in an artifact reference."}


class _ArtifactTool(ResultTool):
    def __init__(self, output_chars: int):
        self.output_chars = output_chars

    @property
    def limit(self) -> int:
        scope = current_artifacts.get()
        return min(MAX_READ_CHARS, scope.output_chars if scope is not None else self.output_chars)


class ReadArtifactTool(_ArtifactTool):
    name = "read_artifact"
    description = "Read an exact text slice from a session artifact. Offsets count Unicode characters from zero."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "artifact_id": _ARTIFACT_ID,
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": self.limit, "default": self.limit},
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        }

    async def execute_result(self, artifact_id: str, offset: int = 0, limit: int | None = None) -> ToolOutcome:
        scope = current_artifacts.get()
        if scope is None:
            return ToolOutcome("No active artifact session.", "refused")
        if limit is None:
            limit = self.limit
        if type(limit) is not int or not 1 <= limit <= self.limit:
            return ToolOutcome(f"Read limit must be 1 to {self.limit}.", "invalid_arguments")
        try:
            metadata, text = await asyncio.to_thread(scope.store.read, artifact_id, offset, limit)
        except InvalidArtifactRequest as exc:
            return ToolOutcome(str(exc), "invalid_arguments")
        except ArtifactError as exc:
            return ToolOutcome(str(exc), "error")
        scope.record("artifact_read", metadata)
        return ToolOutcome(text, artifact_id=artifact_id, artifact_eligible=False)


class SearchArtifactTool(_ArtifactTool):
    name = "search_artifact"
    description = (
        "Search a session artifact for literal, case-sensitive text. "
        "Returns JSON rows [match offset, snippet offset, snippet] using zero-based Unicode character offsets."
    )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "artifact_id": _ARTIFACT_ID,
                "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
            },
            "required": ["artifact_id", "query"],
            "additionalProperties": False,
        }

    async def execute_result(self, artifact_id: str, query: str) -> ToolOutcome:
        scope = current_artifacts.get()
        if scope is None:
            return ToolOutcome("No active artifact session.", "refused")
        try:
            metadata, matches = await asyncio.to_thread(scope.store.search, artifact_id, query)
        except InvalidArtifactRequest as exc:
            return ToolOutcome(str(exc), "invalid_arguments")
        except ArtifactError as exc:
            return ToolOutcome(str(exc), "error")
        scope.record("artifact_search", metadata)

        rows: list[str] = []
        remaining = self.limit - 2
        for match in matches:
            spare = remaining - bool(rows)
            row = json.dumps((match.offset, match.snippet_start, match.snippet), ensure_ascii=False, separators=(",", ":"))
            if len(row) > spare:
                if rows:
                    break
                snippet = match.snippet[match.offset - match.snippet_start:]
                for length in range(min(spare, len(snippet)), -1, -1):
                    row = json.dumps((match.offset, match.offset, snippet[:length]), ensure_ascii=False, separators=(",", ":"))
                    if len(row) <= spare:
                        break
            rows.append(row)
            remaining = spare - len(row)
        return ToolOutcome("[" + ",".join(rows) + "]", artifact_id=artifact_id, artifact_eligible=False)
