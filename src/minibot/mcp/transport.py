"""MCP transport implementations."""

import asyncio
import json
import ntpath
import os
from pathlib import Path
import shutil
from abc import ABC, abstractmethod
from typing import Any

import httpx


class MCPTransport(ABC):
    """Abstract base class for MCP transports."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        pass

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a message and wait for response."""
        pass

    @abstractmethod
    async def notify(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        pass


class StdioTransport(MCPTransport):
    """Transport for stdio-based MCP servers."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd

        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None

    def _resolve_command(self) -> str:
        """Resolve a command into an executable path that works on the current OS."""
        command = (self.command or "").strip()
        if not command:
            raise FileNotFoundError("MCP stdio command is not configured")

        if os.name != "nt":
            return command

        if ntpath.isabs(command) or "\\" in command or "/" in command:
            return command

        for suffix in (".cmd", ".exe", ".bat", ".ps1"):
            resolved = shutil.which(f"{command}{suffix}")
            if resolved:
                return resolved

        resolved = shutil.which(command)
        if resolved:
            return resolved

        return command

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def connect(self) -> None:
        """Start the MCP server process."""
        env = dict(os.environ)
        env.update(self.env)
        executable = self._resolve_command()

        self._process = await asyncio.create_subprocess_exec(
            executable,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self.cwd,
        )

        # Start reading responses
        self._read_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        """Stop the MCP server process."""
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC message and wait for response."""
        if not self.is_connected:
            raise RuntimeError("Transport not connected")

        self._request_id += 1
        request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            **message,
        }

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending[request_id] = future

        # Send request
        data = json.dumps(request) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

        # Wait for response
        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise

    async def notify(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.is_connected:
            raise RuntimeError("Transport not connected")

        notification = {
            "jsonrpc": "2.0",
            **message,
        }
        data = json.dumps(notification) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """Read responses from the server."""
        while self.is_connected:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break

                data = json.loads(line.decode())
                request_id = data.get("id")

                if request_id and request_id in self._pending:
                    future = self._pending.pop(request_id)
                    if "error" in data:
                        future.set_exception(
                            RuntimeError(data["error"].get("message", "Unknown error"))
                        )
                    else:
                        future.set_result(data.get("result", {}))

            except json.JSONDecodeError:
                continue
            except Exception:
                break


class SSETransport(MCPTransport):
    """Transport for SSE-based MCP servers."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = headers or {}
        self._client: httpx.AsyncClient | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def connect(self) -> None:
        """Establish HTTP connection."""
        self._client = httpx.AsyncClient(
            base_url=self.url,
            headers=self.headers,
            timeout=30.0,
        )
        self._connected = True

    async def disconnect(self) -> None:
        """Close HTTP connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a request via HTTP POST."""
        if not self.is_connected:
            raise RuntimeError("Transport not connected")

        response = await self._client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                **message,
            },
        )
        response.raise_for_status()

        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "Unknown error"))

        return data.get("result", {})

    async def notify(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC notification via HTTP POST."""
        if not self.is_connected:
            raise RuntimeError("Transport not connected")

        response = await self._client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                **message,
            },
        )
        response.raise_for_status()
