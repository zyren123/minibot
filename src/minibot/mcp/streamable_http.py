"""Initialize-based MCP Streamable HTTP client transport."""

import asyncio
from contextlib import suppress
import json
from typing import Any

import httpx

from .transport import MCPTransport


STREAMABLE_HTTP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
_ACCEPT = "application/json, text/event-stream"
_CLEANUP_TIMEOUT = 1.0


class StreamableHTTPTransport(MCPTransport):
    """Use a single configured endpoint for MCP requests and session cleanup."""

    def __init__(
        self, url: str | None, headers: dict[str, str] | None = None, *, timeout: float = 30.0,
    ):
        self.url = httpx.URL(url or "")
        if self.url.scheme not in ("http", "https") or not self.url.host or self.url.fragment:
            raise ValueError("Streamable HTTP requires an absolute HTTP or HTTPS MCP endpoint URL")
        if headers is not None and (
            not isinstance(headers, dict)
            or any(not isinstance(name, str) or not isinstance(value, str) for name, value in headers.items())
        ):
            raise ValueError("MCP Streamable HTTP headers must map names to string values")
        self.headers = httpx.Headers(headers or {})
        self.headers.pop("MCP-Session-Id", None)
        self.headers.pop("MCP-Protocol-Version", None)
        self.headers["Accept"] = _ACCEPT
        self.headers["Content-Type"] = "application/json"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._next_id = 0
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._expired = False

    @property
    def is_connected(self) -> bool:
        return self._client is not None and not self._expired

    async def connect(self) -> None:
        if self.is_connected:
            return
        await self.disconnect()
        self._client = httpx.AsyncClient(headers=self.headers, timeout=self._timeout, follow_redirects=False)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        headers = self._session_headers()
        self._session_id = self._protocol_version = None
        self._expired = False
        if client is None:
            return
        try:
            if "MCP-Session-Id" in headers:
                with suppress(httpx.HTTPError, TimeoutError):
                    async with asyncio.timeout(_CLEANUP_TIMEOUT):
                        await client.delete(self.url, headers=headers)
        finally:
            await client.aclose()

    def _session_headers(self) -> dict[str, str]:
        headers = {}
        if self._session_id is not None:
            headers["MCP-Session-Id"] = self._session_id
        if self._protocol_version is not None:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _connected_client(self) -> httpx.AsyncClient:
        if self._client is None or self._expired:
            raise RuntimeError("MCP Streamable HTTP transport is disconnected")
        return self._client

    def _check_response(self, response: httpx.Response, headers: dict[str, str]) -> None:
        if response.status_code == 404 and "MCP-Session-Id" in headers:
            self._expired = True
            self._session_id = self._protocol_version = None
            raise RuntimeError("MCP session expired (HTTP 404). Reconnect before sending another request.")
        if not response.is_success:
            raise RuntimeError(f"MCP Streamable HTTP request failed (HTTP {response.status_code})")

    @staticmethod
    def _decode_message(data: str | bytes) -> dict[str, Any]:
        try:
            message = json.loads(data)
        except (ValueError, RecursionError):
            raise RuntimeError("Malformed MCP JSON-RPC message") from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RuntimeError("Invalid MCP JSON-RPC message")
        return message

    @staticmethod
    def _matches(message: dict[str, Any], request_id: int) -> bool:
        received_id = message.get("id")
        return "method" not in message and type(received_id) is int and received_id == request_id

    @classmethod
    def _result(cls, message: dict[str, Any], request_id: int) -> dict[str, Any]:
        if not cls._matches(message, request_id):
            raise RuntimeError("MCP JSON-RPC response ID does not match the request")
        if "error" in message and "result" not in message:
            error = message["error"]
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                raise RuntimeError(error["message"])
        if "error" in message or not isinstance(message.get("result"), dict):
            raise RuntimeError("Invalid MCP JSON-RPC response")
        return message["result"]

    async def _post_message(
        self, client: httpx.AsyncClient, message: dict[str, Any], headers: dict[str, str],
    ) -> None:
        async with client.stream("POST", self.url, json=message, headers=headers) as response:
            self._check_response(response, headers)
            if response.status_code != 202:
                raise RuntimeError("MCP server did not accept the JSON-RPC notification or response")

    async def _stream_result(
        self, client: httpx.AsyncClient, response: httpx.Response, request_id: int,
    ) -> dict[str, Any]:
        response.encoding = "utf-8"
        lines = []
        first = True
        async for line in response.aiter_lines():
            if first:
                line = line.removeprefix("\ufeff")
                first = False
            if line:
                field, _, value = line.partition(":")
                if field == "data":
                    lines.append(value.removeprefix(" "))
                continue
            if not lines:
                continue
            data = "\n".join(lines)
            lines.clear()
            if not data:
                continue
            message = self._decode_message(data)
            if self._matches(message, request_id):
                return self._result(message, request_id)
            if "method" in message and type(message.get("id")) in (str, int):
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                if message["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "Method not found"}
                await self._post_message(client, reply, self._session_headers())
        raise RuntimeError("MCP SSE stream ended before the matching JSON-RPC response")

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        client = self._connected_client()
        self._next_id += 1
        request_id = self._next_id
        request = {**message, "jsonrpc": "2.0", "id": request_id}
        headers = self._session_headers()
        initializing = request.get("method") == "initialize"

        try:
            async with asyncio.timeout(self._timeout):
                async with client.stream("POST", self.url, json=request, headers=headers) as response:
                    self._check_response(response, headers)
                    if initializing:
                        session_id = response.headers.get("MCP-Session-Id")
                        if session_id is not None and (
                            not session_id or any(not 0x21 <= ord(char) <= 0x7E for char in session_id)
                        ):
                            raise RuntimeError("Invalid MCP session ID")
                        self._session_id = session_id

                    media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if media_type == "application/json":
                        result = self._result(self._decode_message(await response.aread()), request_id)
                    elif media_type == "text/event-stream":
                        result = await self._stream_result(client, response, request_id)
                    else:
                        raise RuntimeError("Expected an MCP JSON or text/event-stream response")
                    if initializing:
                        version = result.get("protocolVersion")
                        if version not in STREAMABLE_HTTP_VERSIONS:
                            raise RuntimeError("Unsupported MCP Streamable HTTP protocol version in initialize response")
                        self._protocol_version = version
                    return result
        except (asyncio.CancelledError, TimeoutError, httpx.TimeoutException):
            if not initializing and self.is_connected:
                with suppress(httpx.HTTPError, RuntimeError, TimeoutError):
                    async with asyncio.timeout(_CLEANUP_TIMEOUT):
                        await self.notify({
                            "method": "notifications/cancelled", "params": {"requestId": request_id},
                        })
            raise

    async def notify(self, message: dict[str, Any]) -> None:
        client = self._connected_client()
        notification = {**message, "jsonrpc": "2.0"}
        async with asyncio.timeout(self._timeout):
            await self._post_message(client, notification, self._session_headers())
