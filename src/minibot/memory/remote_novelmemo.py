"""Access a shared Novel Memo Vault through its MCP endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import NoReturn, TypeGuard, TypeVar

import httpx

from ..config.schema import MCPServerConfig
from ..mcp.client import MCPClient
from .workspace import MemoryBreadcrumb, MemoryDirectory, MemoryDocument, MemoryEntry, MemoryRevisionConflict

DIRECTORY_TOOL = "minibot_memory_directory"
DOCUMENT_TOOL = "minibot_memory_document"
SEARCH_TOOL = "minibot_memory_search"
GRAPH_TOOL = "minibot_memory_graph"
SAVE_TOOL = "minibot_memory_save_document"
PROTOCOL_VERSION = 1
_REQUIRED_TOOLS = frozenset((
    DIRECTORY_TOOL, DOCUMENT_TOOL, SEARCH_TOOL, GRAPH_TOOL, SAVE_TOOL,
    "read_memory", "search_memory", "create_memory", "update_memory", "delete_memory", "manage_triggers",
))
_VALIDATION_TIMEOUT = 6.0
_OPERATION_TIMEOUT = 25.0
_Result = TypeVar("_Result")


class RemoteNovelMemoUnavailable(RuntimeError):
    """The MCP endpoint could not complete the request."""


class RemoteNovelMemoIncompatible(ValueError):
    """The service does not expose the expected Novel Memo tools."""


def _normalized_url(value: str) -> str:
    try:
        url = httpx.URL(value.strip())
        if (
            url.scheme in {"http", "https"}
            and url.host
            and not (url.userinfo or url.query or url.fragment)
        ):
            return str(url)
    except (httpx.InvalidURL, ValueError):
        pass
    raise ValueError("Enter an absolute HTTP or HTTPS MCP URL without credentials, a query, or a fragment")


def _is_string(value: object) -> TypeGuard[str]:
    return isinstance(value, str)


def _is_strings(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(_is_string(item) for item in value)


def _is_breadcrumb(value: object) -> TypeGuard[MemoryBreadcrumb]:
    return isinstance(value, dict) and all(_is_string(value.get(key)) for key in ("id", "title"))


def _is_entry(value: object) -> TypeGuard[MemoryEntry]:
    return (
        isinstance(value, dict)
        and all(_is_string(value.get(key)) for key in ("id", "title", "parent_id"))
        and value.get("kind") in ("folder", "document")
        and "document_id" in value
        and (value["document_id"] is None or _is_string(value["document_id"]))
    )


def _is_entries(value: object) -> TypeGuard[list[MemoryEntry]]:
    return isinstance(value, list) and all(_is_entry(entry) for entry in value)


def _is_directory(value: object) -> TypeGuard[MemoryDirectory]:
    return (
        isinstance(value, dict)
        and isinstance(value.get("breadcrumbs"), list)
        and bool(value["breadcrumbs"])
        and all(_is_breadcrumb(crumb) for crumb in value["breadcrumbs"])
        and _is_entries(value.get("entries"))
    )


def _is_document(value: object) -> TypeGuard[MemoryDocument]:
    return (
        isinstance(value, dict)
        and all(_is_string(value.get(key)) for key in ("id", "title", "filename", "content", "revision"))
        and _is_strings(value.get("triggers"))
        and "disclosure" in value
        and (value["disclosure"] is None or _is_string(value["disclosure"]))
    )


def _is_search_results(value: object) -> TypeGuard[list[dict]]:
    return isinstance(value, list) and all(isinstance(row, dict) and _is_string(row.get("uri")) for row in value)


def _raise_remote_error(code: object) -> NoReturn:
    if code == "conflict":
        raise MemoryRevisionConflict("Memory changed since it was opened")
    if code == "not_found":
        raise FileNotFoundError("Memory was not found on the Novel Memo service")
    if code == "invalid":
        raise ValueError("The Novel Memo service rejected the memory path or input")
    if code == "forbidden":
        raise PermissionError("Permission denied on the Novel Memo service")
    raise RemoteNovelMemoUnavailable("The Novel Memo MCP service could not complete the operation")


class RemoteNovelMemoWorkspace:
    """A stateless client for the Agent and WebUI operations on one remote Vault."""

    def __init__(self, url: str):
        self.url = _normalized_url(url)
        endpoint_id = hashlib.sha256(self.url.encode("utf-8")).hexdigest()
        self._revision_prefix = f"mcp-v1:{endpoint_id}:"

    @asynccontextmanager
    async def _session(self, timeout: float) -> AsyncIterator[MCPClient]:
        client = MCPClient(MCPServerConfig(name="novelmemo", transport="streamable_http", url=self.url))
        try:
            async with asyncio.timeout(timeout):
                try:
                    await client.connect()
                except Exception:
                    raise RemoteNovelMemoUnavailable("Could not connect to the Novel Memo MCP service") from None
                yield client
        except TimeoutError:
            raise RemoteNovelMemoUnavailable("The Novel Memo MCP service timed out") from None
        finally:
            with suppress(Exception):
                await client.disconnect()

    async def validate(self) -> None:
        async with self._session(_VALIDATION_TIMEOUT) as client:
            if not _REQUIRED_TOOLS.issubset(tool.name for tool in client.get_tools()):
                raise RemoteNovelMemoIncompatible("The MCP service does not provide the Novel Memo workspace tools")
            root = await self._invoke(client, DIRECTORY_TOOL, {"path": ""}, _is_directory)
            if len(root["breadcrumbs"]) != 1 or root["breadcrumbs"][0]["id"] != "":
                raise RemoteNovelMemoIncompatible("The MCP service returned an invalid Novel Memo root directory")

    @staticmethod
    async def _invoke(
        client: MCPClient, name: str, arguments: dict, check: Callable[[object], TypeGuard[_Result]],
    ) -> _Result:
        result = await client.call_tool(name, arguments)
        if result.is_error:
            raise RemoteNovelMemoUnavailable("The Novel Memo MCP service could not complete the operation")
        try:
            response = json.loads(result.content)
        except (ValueError, RecursionError):
            raise RemoteNovelMemoIncompatible("The MCP service returned an invalid Novel Memo response") from None
        if (
            not isinstance(response, dict)
            or type(response.get("version")) is not int
            or response["version"] != PROTOCOL_VERSION
        ):
            raise RemoteNovelMemoIncompatible("The MCP service returned an unsupported Novel Memo response")
        if "error" in response:
            _raise_remote_error(response["error"])
        value = response.get("value")
        if not check(value):
            raise RemoteNovelMemoIncompatible("The MCP service returned an invalid Novel Memo response")
        return value

    async def _call(self, name: str, arguments: dict, check: Callable[[object], TypeGuard[_Result]]) -> _Result:
        async with self._session(_OPERATION_TIMEOUT) as client:
            return await self._invoke(client, name, arguments, check)

    def _scope_revision(self, document: MemoryDocument) -> MemoryDocument:
        return {**document, "revision": self._revision_prefix + document["revision"]}

    async def directory(self, path: str) -> MemoryDirectory:
        return await self._call(DIRECTORY_TOOL, {"path": path}, _is_directory)

    async def document(self, path: str) -> MemoryDocument:
        return self._scope_revision(await self._call(DOCUMENT_TOOL, {"path": path}, _is_document))

    async def save_document(
        self, path: str, content: str, revision: str, *,
        triggers: list[str] | None = None, disclosure: str | None = None,
    ) -> MemoryDocument:
        if not revision.startswith(self._revision_prefix):
            raise MemoryRevisionConflict("The memory source changed since the document was opened")
        saved = await self._call(SAVE_TOOL, {
            "path": path, "content": content, "revision": revision.removeprefix(self._revision_prefix),
            "triggers": triggers, "disclosure": disclosure,
        }, _is_document)
        return self._scope_revision(saved)

    async def search(self, query: str) -> list[MemoryEntry]:
        return await self._call(SEARCH_TOOL, {"query": query}, _is_entries)

    async def graph(self) -> list[MemoryEntry]:
        return await self._call(GRAPH_TOOL, {}, _is_entries)

    async def read_memory(self, uri: str) -> str:
        return await self._call("read_memory", {"uri": uri}, _is_string)

    async def search_memory(
        self, query: str, *, domain: str | None = None, limit: int = 10,
    ) -> list[dict]:
        return await self._call("search_memory", {"query": query, "domain": domain, "limit": limit}, _is_search_results)

    async def create_memory(
        self, parent_uri: str, content: str, disclosure: str, title: str | None = None,
    ) -> str:
        return await self._call("create_memory", {
            "parent_uri": parent_uri, "content": content, "disclosure": disclosure, "title": title,
        }, _is_string)

    async def update_memory(
        self, uri: str, *, old_string: str | None = None, new_string: str | None = None,
        append: str | None = None, disclosure: str | None = None,
    ) -> str:
        return await self._call("update_memory", {
            "uri": uri, "old_string": old_string, "new_string": new_string, "append": append, "disclosure": disclosure,
        }, _is_string)

    async def delete_memory(self, uri: str) -> str:
        return await self._call("delete_memory", {"uri": uri}, _is_string)

    async def manage_triggers(
        self, uri: str, *, add: list[str] | None = None, remove: list[str] | None = None,
    ) -> list[str]:
        return await self._call("manage_triggers", {"uri": uri, "add": add, "remove": remove}, _is_strings)
