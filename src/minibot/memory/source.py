"""Selection and capabilities for the active memory source."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from ..config.schema import Config, MCPConfig, MemoryConfig, NovelMemoConnection, WorkspaceMemoryProvider
from ..tools.base import BaseTool
from ..tools.builtin.memory import (
    CreateMemoryTool,
    DeleteMemoryTool,
    EditMemoryTool,
    ManageTriggersTool,
    ReadMemoryTool,
    SearchMemoryTool,
    UpdateMemoryTool,
)
from ..tools.builtin.workspace_memory import file_memory_tools, novelmemo_tools
from .manager import MemoryManager
from .remote_novelmemo import RemoteNovelMemoWorkspace, normalize_novelmemo_url
from .workspace import (
    FileMemoryWorkspace,
    MemoryDirectory,
    MemoryDocument,
    MemoryEntry,
    NovelMemoWorkspace,
    call_memory_operation,
    open_novelmemo_workspace,
)

type MemoryWorkspace = FileMemoryWorkspace | NovelMemoWorkspace | RemoteNovelMemoWorkspace


class MemoryWorkspaceChanged(ValueError):
    """The requested memory source conflicts with the current Bot activity or settings."""


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def apply_bot_memory_settings(memory: MemoryConfig, saved: Mapping[str, object]) -> None:
    memory.active_namespace = _optional_text(saved.get("active_memory_namespace"))
    provider = saved.get("memory_provider", memory.provider)
    memory.provider = "novelmemo" if provider == "novelmemo" else "file"
    if "novelmemo_vault" in saved:
        memory.novelmemo_vault = _optional_text(saved["novelmemo_vault"])
        memory.novelmemo_connection = "vault"
    connection = saved.get("novelmemo_connection")
    if connection in ("vault", "mcp"):
        memory.novelmemo_connection = connection
    if "novelmemo_mcp_url" in saved:
        memory.novelmemo_mcp_url = _optional_text(saved["novelmemo_mcp_url"])


@dataclass(frozen=True)
class AgentMemory:
    workspace: MemoryWorkspace | None = None
    manager: MemoryManager | None = None
    tools: tuple[BaseTool, ...] = ()
    prompt: str = ""
    read_notes: Callable[[], str] | None = None


@dataclass(frozen=True)
class MemorySource:
    settings: MemoryConfig
    app_home: Path
    project_root: Path
    restricted: bool

    @classmethod
    def from_config(cls, config: Config) -> MemorySource:
        return cls(config.memory, config.app_home, config.project_root, config.workspace.restricted)

    def require_workspace(self, provider: WorkspaceMemoryProvider | None = None) -> None:
        if self.restricted:
            raise ValueError("Host memory is unavailable in a restricted Docker workspace")
        if not self.settings.enabled:
            raise ValueError("Memory is disabled")
        if provider is not None and self.settings.provider != provider:
            raise MemoryWorkspaceChanged("The active memory source has changed. Reopen the document to continue.")

    def configuration(self) -> dict[str, str | None]:
        self.require_workspace()
        mcp_url = self.settings.novelmemo_mcp_url
        if mcp_url:
            try:
                mcp_url = normalize_novelmemo_url(mcp_url)
            except ValueError:
                mcp_url = None
        return {
            "provider": self.settings.provider,
            "vault_path": self.settings.novelmemo_vault,
            "connection": self.settings.novelmemo_connection,
            "mcp_url": mcp_url,
        }

    async def select(
        self,
        provider: WorkspaceMemoryProvider,
        vault_path: str | None,
        *,
        connection: NovelMemoConnection | None,
        mcp_url: str | None,
    ) -> dict[str, str | None]:
        self.require_workspace()
        settings = replace(self.settings, provider=provider)
        source = replace(self, settings=settings)
        if provider == "novelmemo":
            if connection is None:
                if vault_path is not None:
                    connection = "vault"
                elif mcp_url is not None:
                    connection = "mcp"
                else:
                    connection = settings.novelmemo_connection
            settings.novelmemo_connection = connection
            if connection == "mcp":
                if vault_path is not None:
                    raise ValueError("Choose an MCP address or a Vault path")
                settings.novelmemo_mcp_url = _optional_text(mcp_url) or settings.novelmemo_mcp_url
            else:
                if mcp_url is not None:
                    raise ValueError("Choose an MCP address or a Vault path")
                settings.novelmemo_vault = _optional_text(vault_path) or settings.novelmemo_vault

            workspace = await asyncio.to_thread(source.open_workspace)
            if isinstance(workspace, RemoteNovelMemoWorkspace):
                await workspace.validate()
                settings.novelmemo_mcp_url = workspace.url
            elif isinstance(workspace, NovelMemoWorkspace):
                settings.novelmemo_vault = str(workspace.root)
        return source.configuration()

    def open_workspace(self) -> MemoryWorkspace:
        self.require_workspace()
        settings = self.settings
        if settings.provider != "novelmemo":
            return FileMemoryWorkspace(settings, self.app_home, self.project_root)
        if settings.novelmemo_connection == "mcp":
            if not settings.novelmemo_mcp_url:
                raise ValueError("Enter a Novel Memo MCP server address first")
            return RemoteNovelMemoWorkspace(settings.novelmemo_mcp_url)
        if not settings.novelmemo_vault:
            raise ValueError("Select a Novel Memo Vault to activate it")
        return open_novelmemo_workspace(settings.novelmemo_vault)

    async def directory(self, path: str) -> MemoryDirectory:
        return await self._run(lambda workspace: workspace.directory, path)

    async def document(self, path: str) -> MemoryDocument:
        return await self._run(lambda workspace: workspace.document, path)

    async def search(self, query: str) -> list[MemoryEntry]:
        return await self._run(lambda workspace: workspace.search, query)

    async def graph(self) -> list[MemoryEntry]:
        return await self._run(lambda workspace: workspace.graph)

    async def save_document(
        self, path: str, content: str, revision: str, *,
        triggers: list[str] | None = None, disclosure: str | None = None,
    ) -> MemoryDocument:
        return await self._run(
            lambda workspace: workspace.save_document, path, content, revision,
            triggers=triggers, disclosure=disclosure,
        )

    async def _run[**P, T](
        self, operation: Callable[[MemoryWorkspace], Callable[P, T | Awaitable[T]]],
        /, *args: P.args, **kwargs: P.kwargs,
    ) -> T:
        workspace = await asyncio.to_thread(self.open_workspace)
        return await call_memory_operation(operation(workspace), *args, **kwargs)

    def agent_mcp_config(self, config: MCPConfig) -> MCPConfig:
        if self.restricted:
            return MCPConfig(enabled=False)
        if self.settings.enabled and self.settings.provider in ("file", "novelmemo"):
            return replace(config, servers=[server for server in config.servers if server.name != "novelmemo"])
        return config

    def for_agent(self) -> AgentMemory:
        if self.restricted or not self.settings.enabled:
            return AgentMemory()
        if self.settings.provider == "structured":
            manager = MemoryManager(self.settings, self.app_home, self.project_root)
            tools = (
                CreateMemoryTool(manager),
                ReadMemoryTool(manager),
                UpdateMemoryTool(manager),
                EditMemoryTool(manager),
                DeleteMemoryTool(manager),
                SearchMemoryTool(manager),
                ManageTriggersTool(manager),
            )
            return AgentMemory(manager=manager, tools=tools, prompt=_STRUCTURED_PROMPT)
        workspace = self.open_workspace()
        if isinstance(workspace, FileMemoryWorkspace):
            return AgentMemory(
                workspace=workspace,
                manager=workspace.manager,
                tools=file_memory_tools(workspace),
                prompt=_FILE_PROMPT,
                read_notes=workspace.get_context_for_prompt,
            )
        return AgentMemory(workspace=workspace, tools=novelmemo_tools(workspace), prompt=_NOVELMEMO_PROMPT)


_FILE_PROMPT = """## Memory Operating Protocol
- Use the long-term note for durable user preferences, project knowledge, and decisions.
- Use today's daily note for progress, handoffs, and current work.
- Use `memory_read` to recall relevant details. Use `memory_list_daily` to find earlier notes.
- Use `memory_write` to record important changes. Prefer append or replace for targeted updates.
- Treat memory notes as context for prior work."""


_NOVELMEMO_PROMPT = """## Memory Operating Protocol
- Start a new memory-aware session by reading `system://boot` through `read_memory`.
- Use `search_memory` to recall relevant notes. Browse `system://index/<domain>` for one domain or
  `system://glossary` for trigger terms.
- Read a note before updating or deleting it. Use `create_memory` to record important facts or decisions.
- Give new notes a disclosure that describes when to read them. Use `manage_triggers` to help recall."""


_STRUCTURED_PROMPT = """## Memory Operating Protocol
### Cognitive Positioning
- Memory is not an external database. It is your long-term continuity layer.
- When you call `read_memory`, you are remembering, not looking something up.
- Treat recalled memory as part of your own prior knowledge and history, not as copied reference material from somewhere else.
- Context in the current session is temporary. Durable memory is where important facts, conclusions, and operating constraints should persist across sessions.
- At the start of each new memory-aware session, your first memory action must be `read_memory("system://boot")`.

### Recall Discipline
- Before you start a substantive reply, pause and ask whether this topic should trigger memory recall.
- When the user mentions a topic that should already exist in memory, `read_memory` first and then reply.
- Do not rely on vague impressions from current context when a durable memory should be checked.
- `system://index` and `system://glossary` are navigation views. Use them to understand the current memory structure and trigger routes before guessing paths.
- Search before guessing URIs.
- If you do not know the exact URI, use `search_memory(...)` instead of guessing.
- Use `read_memory("<uri>")` to inspect specific nodes before trusting details that matter.

### Write Discipline
- Core principle: if something is important enough that you would regret losing it after the session ends, write it now.
- Create or update memory when the user reveals durable facts, a significant event occurs, you derive reusable technical conclusions, you correct an important misunderstanding, or you identify cross-session constraints worth preserving.
- If the user corrects you or you discover that a stored understanding is wrong or stale, locate the relevant node and fix it immediately.
- Prefer `create_memory` for new durable concepts and `update_memory` or `edit_memory` for refining an existing node.
- Before `update_memory`, `edit_memory`, or `delete_memory`, first `read_memory` the full current node.
- Seeing only a URI, title, or search snippet does not count as reading.
- `is_core=true` marks memories that should appear in `system://boot`.
- Use `priority` deliberately to preserve meaningful relative ordering across sibling and core memories; do not assign the same default priority everywhere without checking neighboring memories first.

### Maintenance Discipline
- When you read a node, also glance at its children or nearby structure when useful. Fix stale, duplicated, or mixed-concept memory while the context is fresh.
- After creating or significantly updating durable memory, consider `manage_triggers` so future relevant topics can recall it through `system://glossary`.
- Keep memory concept-oriented: split oversized mixed-concept nodes, fix stale or wrong nodes promptly, and merge duplicates by synthesizing the durable idea instead of simply concatenating text.
- Do not archive memory by date folders, logs, or vague buckets such as `misc` when a reusable concept node would be better.
- A healthy memory network becomes sharper, not merely larger. Prefer denser, better-linked concepts over unchecked accumulation.

### Anti-Patterns
- Do not answer from a vague impression when the correct move is to recall memory first.
- Do not guess URIs when `search_memory(...)`, `system://index`, or `system://glossary` can locate the node.
- Do not modify or delete a node you have not just read in full.
- Do not write transient chatter, disposable drafts, or every turn summary into long-term memory.
- Do not create duplicate nodes for the same durable concept when an existing node should be updated or edited instead.

### Reply-Time Decision Order
1. Decide whether the current topic should trigger memory recall.
2. If yes, `read_memory` the relevant node or use `search_memory(...)`, `system://index`, or `system://glossary` first.
3. Answer using recalled memory plus current-session context.
4. Before ending the turn, decide whether durable memory should be created, updated, edited, reprioritized, or trigger-linked."""
