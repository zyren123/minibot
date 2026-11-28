"""Serve an existing Novel Memo Vault as a Streamable HTTP MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from functools import wraps
from inspect import signature

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .remote_novelmemo import DIRECTORY_TOOL, DOCUMENT_TOOL, GRAPH_TOOL, PROTOCOL_VERSION, SAVE_TOOL, SEARCH_TOOL
from .workspace import MemoryRevisionConflict, NovelMemoWorkspace, open_novelmemo_workspace

_LOOPBACK_NAMES = ("localhost", "127.0.0.1", "::1")
_NOT_FOUND_MESSAGES = ("Memory folder not found", "Memory document not found")


def _as_mcp_tool(operation: Callable):
    @wraps(operation)
    async def run(**arguments) -> str:
        try:
            value = await asyncio.to_thread(operation, **arguments)
        except MemoryRevisionConflict:
            result = {"error": "conflict"}
        except FileNotFoundError:
            result = {"error": "not_found"}
        except PermissionError:
            result = {"error": "forbidden"}
        except ValueError as exc:
            result = {"error": "not_found" if str(exc) in _NOT_FOUND_MESSAGES else "invalid"}
        except OSError:
            result = {"error": "unavailable"}
        else:
            result = {"value": value}
        return json.dumps({"version": PROTOCOL_VERSION, **result}, ensure_ascii=False)

    # FastMCP builds each input schema from the original operation.
    setattr(run, "__signature__", signature(operation, eval_str=True).replace(return_annotation=str))
    return run


def create_novelmemo_mcp(
    workspace: NovelMemoWorkspace, *, host: str = "127.0.0.1", port: int = 8767,
) -> FastMCP:
    security = None
    if host in _LOOPBACK_NAMES:
        names = ("localhost", "127.0.0.1", "[::1]")
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[*names, *(f"{name}:*" for name in names)],
            allowed_origins=[f"http://{name}:*" for name in names],
        )
    server = FastMCP(
        "Novel Memo", host=host, port=port, stateless_http=True,
        json_response=True, transport_security=security,
    )
    tools = (
        ("read_memory", workspace.read_memory, "Read a memory URI or a Novel Memo system view."),
        ("search_memory", workspace.search_memory, "Search memory by query and optional domain."),
        ("create_memory", workspace.create_memory, "Create a child memory with its disclosure."),
        ("update_memory", workspace.update_memory, "Replace or append memory text, or update its disclosure."),
        ("delete_memory", workspace.delete_memory, "Delete one memory by URI."),
        ("manage_triggers", workspace.manage_triggers, "Add or remove retrieval triggers for a memory."),
        (DIRECTORY_TOOL, workspace.directory, "List direct entries and breadcrumbs at a workspace path."),
        (DOCUMENT_TOOL, workspace.document, "Read a memory document with its revision and properties."),
        (SEARCH_TOOL, workspace.search, "Search the workspace for matching documents."),
        (GRAPH_TOOL, workspace.graph, "List the workspace entries with their parent relationships."),
        (SAVE_TOOL, workspace.save_document, "Save a memory document if its revision still matches."),
    )
    for name, operation, description in tools:
        server.tool(name=name, description=description)(_as_mcp_tool(operation))
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, help="Absolute path to an initialized Novel Memo Vault")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8767, help="Listener port (default: 8767)")
    args = parser.parse_args()
    try:
        workspace = open_novelmemo_workspace(args.vault)
    except ValueError as exc:
        parser.error(str(exc))
    create_novelmemo_mcp(workspace, host=args.host, port=args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
