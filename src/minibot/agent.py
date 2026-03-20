"""Main Agent implementation."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from contextlib import suppress
from datetime import datetime
import json
import uuid
from typing import Any, Literal

from .config import Config, get_config
from .core.client import LLMClient
from .core.tool_args import parse_tool_arguments
from .tools.registry import ToolRegistry
from .tools.builtin import (
    BashTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    TodoWriteTool,
    TodoManager,
    MemoryReadTool,
    MemoryWriteTool,
)
from .memory.manager import MemoryManager
from .tools.meta import (
    TaskTool,
    SkillTool,
    TeamCreateTool,
    TeamMembersTool,
    TeamTaskTool,
    TeamMessageTool,
    TeamBroadcastTool,
    TeamWaitTool,
    TeamShutdownTool,
)
from .skills.loader import SkillLoader
from .subagents.registry import AgentRegistry
from .subagents.executor import SubagentExecutor
from .hooks.manager import HookManager
from .mcp.manager import MCPManager
from .teams.context import TeamExecutionContext, team_execution_context
from .teams.runtime import TeamRuntime
from .events import EventSink, StreamEvent
from .utils.output import (
    print_assistant,
    print_tool_call,
    print_tool_output,
    print_system,
    status,
    stream_assistant_start,
    stream_assistant_write,
    stream_assistant_end,
)

AgentRole = Literal["solo", "lead", "teammate"]


class UserInterruptedError(Exception):
    """Raised when the user interrupts the active model generation."""


def _format_tool_args(args: dict[str, Any], max_len: int = 80) -> str:
    """Format tool arguments for display."""
    parts = []
    for key, value in args.items():
        if isinstance(value, str):
            if len(value) > 30:
                value = value[:27] + "..."
            parts.append(f'{key}="{value}"')
        elif isinstance(value, bool):
            parts.append(f"{key}={value}")
        elif isinstance(value, (int, float)):
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}=<{type(value).__name__}>")

    result = ", ".join(parts)
    if len(result) > max_len:
        result = result[: max_len - 3] + "..."
    return result


class Agent:
    """Main agent with tools, skills, hooks, MCP support, and optional team mode."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        role: AgentRole = "solo",
        session_id: str | None = None,
        extra_system_prompt: str | None = None,
        event_sink: EventSink | None = None,
        skills_dir: Any = None,
        disabled_skills: Any = None,
        team_runtime: TeamRuntime | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
        allow_subagent_delegation: bool = True,
        allow_team_tools: bool = True,
    ):
        if role not in {"solo", "lead", "teammate"}:
            raise ValueError(f"Unknown role: {role}")

        self.config = config or get_config()
        self.workdir = self.config.workdir
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.extra_system_prompt = (extra_system_prompt or "").strip()
        self.event_sink = event_sink
        self.role: AgentRole = role
        self.team_role: Literal["lead", "teammate"] = "teammate" if role == "teammate" else "lead"
        self.team_id = team_id
        self.member_id = member_id
        self.allow_subagent_delegation = bool(allow_subagent_delegation) and role != "teammate"
        self.allow_team_tools = bool(allow_team_tools)
        self.quiet_teammates = self.config.teams.quiet_teammates
        self.debug_teammate_output = self.config.teams.debug_teammate_output
        teammate_quiet = role == "teammate" and self.quiet_teammates and not self.debug_teammate_output
        self.silent = teammate_quiet
        self.status_enabled = not teammate_quiet
        self.stream_enabled = bool(self.config.llm.stream_enabled)
        self.stream_degraded = False

        self.client = LLMClient(
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key,
            model=self.config.llm.model,
        )

        resolved_skills_dir = skills_dir if skills_dir is not None else self.config.skills_dir
        self._skills_dir_source = resolved_skills_dir
        self.skill_loader = SkillLoader(resolved_skills_dir, disabled_skills=disabled_skills)
        self.todo_manager = TodoManager()
        self.agent_registry = AgentRegistry()
        self.agent_registry.apply_config(self.config.subagents)
        self.tool_registry = ToolRegistry()

        self.hook_manager = HookManager(self.config.hooks, self.workdir)
        self.mcp_manager = MCPManager(self.config.mcp, self.workdir)

        self.memory_manager = (
            MemoryManager(
                self.config.memory,
                self.config.app_home,
                self.config.project_root,
            )
            if self.config.memory.enabled
            else None
        )
        if self.memory_manager is not None:
            notice = self.memory_manager.migration_notice()
            if notice:
                if self._ui_enabled():
                    print_system(f"Warning: {notice}")

        self.team_runtime: TeamRuntime | None = team_runtime
        self._owns_team_runtime = False
        if self.allow_team_tools and self.config.teams.enabled and self.role in {"solo", "lead"}:
            if self.team_runtime is None:
                self.team_runtime = TeamRuntime(
                    config=self.config.teams,
                    workdir=self.workdir,
                    hook_manager=self.hook_manager,
                )
                self._owns_team_runtime = True
            self.team_runtime.set_agent_factory(self._create_teammate_agent)

        self.subagent_executor = SubagentExecutor(
            client=self.client,
            tool_registry=self.tool_registry,
            agent_registry=self.agent_registry,
            hook_manager=self.hook_manager,
            workdir=self.workdir,
            skill_loader=self.skill_loader,
        )

        self._register_tools()
        self.system_prompt = self._build_system_prompt()

    def _ui_enabled(self) -> bool:
        """Whether terminal rendering should be used."""
        return self.event_sink is None and not self.silent

    async def _emit(self, event: StreamEvent) -> None:
        """Emit one structured event if an event sink is configured."""
        if self.event_sink is None:
            return
        try:
            # Ensure the session_id is always present for consumers.
            event.setdefault("session_id", self.session_id)
            await self.event_sink.emit(event)
        except Exception:
            # Event emission must not break the agent runtime.
            return

    def set_stream_enabled(self, enabled: bool) -> None:
        """Toggle streaming output for the current session."""
        self.stream_enabled = bool(enabled)
        if self.stream_enabled:
            # Re-enable stream attempts after an earlier degraded fallback.
            self.stream_degraded = False

    def set_disabled_skills(self, disabled_skills: list[str]) -> None:
        """Update the set of disabled skills and refresh dependent components."""
        self.skill_loader = SkillLoader(
            self._skills_dir_source,
            disabled_skills=disabled_skills,
        )

        tool = self.tool_registry.get("Skill")
        if tool is not None and hasattr(tool, "skill_loader"):
            tool.skill_loader = self.skill_loader

        self.subagent_executor.skill_loader = self.skill_loader
        self.refresh_system_prompt()

    def refresh_system_prompt(self) -> None:
        """Rebuild the current system prompt after capability changes."""
        self.system_prompt = self._build_system_prompt()

    def reset_session(self, session_id: str) -> None:
        """Update the session id (used when switching sessions)."""
        self.session_id = session_id

    def _streaming_active(self) -> bool:
        """Whether this agent should attempt streaming responses now."""
        if self.role == "teammate":
            return False
        if not self.stream_enabled:
            return False
        return not self.stream_degraded

    def get_stream_state(self) -> dict[str, Any]:
        """Streaming status for banners, /info, and /stream command."""
        if not self.stream_enabled:
            mode = "off"
        elif self.stream_degraded:
            mode = "degraded"
        else:
            mode = "on"
        return {
            "mode": mode,
            "enabled": self.stream_enabled,
            "degraded": self.stream_degraded,
            "active": self._streaming_active(),
        }

    def _assistant_title(self) -> str:
        if self.role == "teammate" and self.member_id and self.debug_teammate_output:
            return f"Assistant {self.member_id}"
        return "Assistant"

    def _tool_log_prefix(self) -> str:
        if self.role == "teammate" and self.member_id and self.debug_teammate_output:
            return f"[{self.member_id}] "
        return ""

    def _tool_output_name(self, name: str) -> str:
        if self.role == "teammate" and self.member_id and self.debug_teammate_output:
            return f"{name} ({self.member_id})"
        return name

    def _create_teammate_agent(self, team_id: str, member_id: str) -> "Agent":
        """Factory used by TeamRuntime to create teammate workers."""
        return Agent(
            config=self.config,
            role="teammate",
            team_runtime=self.team_runtime,
            team_id=team_id,
            member_id=member_id,
        )

    def _register_tools(self) -> None:
        """Register all available tools."""
        self.tool_registry.register(BashTool(self.workdir, self.config.tools.timeout))
        self.tool_registry.register(ReadFileTool(self.workdir))
        self.tool_registry.register(WriteFileTool(self.workdir))
        self.tool_registry.register(EditFileTool(self.workdir))
        self.tool_registry.register(TodoWriteTool(self.todo_manager))
        self.tool_registry.register(SkillTool(self.skill_loader))

        # Teammates are explicitly blocked from recursive delegation.
        if self.allow_subagent_delegation:
            self.tool_registry.register(TaskTool(self.agent_registry, self.subagent_executor))

        if self.memory_manager is not None:
            self.tool_registry.register(MemoryReadTool(self.memory_manager))
            self.tool_registry.register(MemoryWriteTool(self.memory_manager))

        if self.allow_team_tools and self.config.teams.enabled and self.team_runtime is not None:
            self.tool_registry.register(TeamMembersTool(self.team_runtime))
            self.tool_registry.register(
                TeamTaskTool(self.team_runtime)
            )
            self.tool_registry.register(
                TeamMessageTool(self.team_runtime)
            )
            self.tool_registry.register(
                TeamBroadcastTool(self.team_runtime)
            )

            if self.role in {"solo", "lead"}:
                self.tool_registry.register(
                    TeamCreateTool(
                        self.team_runtime,
                        default_members=self.config.teams.default_members,
                    )
                )
                self.tool_registry.register(
                    TeamWaitTool(
                        self.team_runtime,
                        default_timeout_sec=self.config.teams.wait_timeout_sec,
                    )
                )
                self.tool_registry.register(TeamShutdownTool(self.team_runtime))

    async def connect_mcp_servers(self) -> dict[str, Exception | None]:
        """Connect to all configured MCP servers."""
        errors = await self.mcp_manager.connect_all()

        tool_count = self.mcp_manager.register_tools(self.tool_registry)
        if tool_count > 0:
            self.refresh_system_prompt()

        return errors

    async def disconnect_mcp_servers(self) -> None:
        """Disconnect from all MCP servers."""
        await self.mcp_manager.disconnect_all()

    async def close_client(self) -> None:
        """Close LLM client resources."""
        await self.client.close()

    def _build_system_prompt(self) -> str:
        """Build the system prompt following Professional ReAct standards."""
        mcp_section = ""
        if self.mcp_manager.server_count > 0:
            server_list = ", ".join(self.mcp_manager.list_servers())
            mcp_section = f"""
## Extended Capabilities (MCP)
Connected Servers: {server_list}
Note: MCP tools are namespaced with `mcp__<server>__<tool>`. Use them to interact with external services or context."""

        memory_section = ""
        if self.memory_manager is not None:
            memory_content = self.memory_manager.get_context_for_prompt()
            if memory_content:
                memory_section = f"""

## Memory (Auto-loaded)
{memory_content}

Use `memory_write` tool to update memories as you work:
- **long_term**: Save stable patterns, user preferences, key decisions, project conventions
- **daily**: Save today's progress, current context, continuations for next session
Keep long-term memory concise (<{self.config.memory.long_term_max_lines} lines). Daily memory is for ephemeral context."""

        subagent_section = ""
        if self.allow_subagent_delegation:
            subagent_section = f"""
2. **Specialized Subagents** (Delegation):
   {self.agent_registry.get_descriptions()}
   *Rule: Use the `Task` tool to delegate broad, ambiguous, or multi-step sub-problems to these agents.*
   *Agent Selection Guidelines:*
   - Use `explore` agent when user requests involve: exploring, analyzing, searching, reading, understanding, or discovering code/files
   - Use `plan` agent when user requests involve: planning, designing, strategizing, or outlining implementation steps
   - Use `code` agent when user requests involve: implementing, coding, fixing, modifying, or creating code/files
"""

        team_section = ""
        if self.allow_team_tools and self.role in {"solo", "lead"} and self.config.teams.enabled:
            team_section = """
## Team Orchestration Policy
- You can decide whether to create a team (`TeamCreate`) and how many teammates to spawn.
- Prefer teams for parallelizable work: cross-module changes, competing debugging hypotheses, or multi-perspective reviews.
- Prefer solo execution for short sequential edits or same-file heavy changes.
- Typical teammate count is 2-6. Start small unless clear parallelism exists.
- Use `TeamTask`, `TeamMessage`, `TeamBroadcast`, and `TeamWait` to coordinate and synthesize results.
"""
        elif self.allow_team_tools and self.role == "teammate":
            team_section = f"""
## Team Role Constraints
- You are teammate `{self.member_id}` in team `{self.team_id}`.
- You MUST NOT create teammates or delegate with `Task`.
- Coordinate via `TeamMessage`, `TeamBroadcast`, and `TeamTask`.
- Keep updates concise and unblock other teammates quickly.
"""

        prompt = f"""You are an advanced autonomous Coding Agent operating in `{self.workdir}`. your name is Minibot.
Your goal is to solve complex software engineering tasks by following a strict ReAct (Reason -> Act -> Observe) loop.

## Environment Context
- Working Directory: {self.workdir}
- User: Current active developer
- Date: {datetime.now().strftime("%Y-%m-%d")}
- Role: {self.role}

## Tool Capability Hierarchy
You have access to layered capabilities. Choose the most specific tool for the job:

1. **Core Skills** (High Precision):
   {self.skill_loader.get_descriptions()}
   *Rule: If a user request matches a Skill description, prefer the Skill tool over manual steps.*
{subagent_section}
3. **Standard & MCP Tools** (Atomic Actions):
   Standard file/shell tools plus: {mcp_section}

## The ReAct Protocol
You must not act without reasoning. For every step, follow this process:

1. **Analyze**: Understand the current state. Read files or list directories if you lack context. read files first before editing them.
2. **Plan**: Formulate a step-by-step plan. Use `TodoWrite` to persist the state of complex tasks.
3. **Reason**: Explain *why* you are choosing a specific tool or action next.
4. **Act**: Invoke the tool.
5. **Observe**: Analyze the tool output. If it failed, reason about the error and try a different approach.

## Operational Rules
- **Context First**: Do not hallucinate file contents. Always read a file before editing it.
- **Idempotency**: Ensure your edits are safe. Prefer patching or rewriting over blind appending.
- **Progress Tracking**: Update your `TodoWrite` list as you complete steps.
- **Communication**: When the task is complete, provide a concise summary of changes made.
- **Fail Gracefully**: If a path is blocked, stop and ask the user or try an alternative strategy. Do not loop endlessly.
{team_section}
{memory_section}
You are now live. Await instructions and begin the ReAct loop."""

        if self.extra_system_prompt:
            prompt = f"{prompt}\n\n{self.extra_system_prompt}\n"

        return prompt

    async def _execute_tool_with_hooks(
        self,
        name: str,
        args: dict[str, Any],
    ) -> str:
        """Execute a tool with pre/post hooks."""
        blocked, reason = await self.hook_manager.trigger_pre_tool_call(name, args)
        if blocked:
            return f"Blocked by hook: {reason}"

        ctx = TeamExecutionContext(
            role=self.team_role,
            team_id=self.team_id,
            member_id=self.member_id,
            runtime=self.team_runtime,
        )
        with team_execution_context(ctx):
            result = await self.tool_registry.execute(name, args)

        await self.hook_manager.trigger_post_tool_call(name, args, result)
        return result

    @staticmethod
    def _drain_interrupt_queue(interrupt_queue: "asyncio.Queue[None] | None") -> None:
        if interrupt_queue is None:
            return
        while True:
            try:
                interrupt_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @classmethod
    def _nested_container_values(cls, value: Any) -> list[Any]:
        nested: list[Any] = []
        for key in (
            "delta",
            "message",
            "content",
            "contents",
            "output",
            "outputs",
            "items",
            "parts",
            "response",
            "responses",
            "result",
            "results",
            "candidate",
            "candidates",
            "choices",
        ):
            item = cls._field(value, key)
            if item is not None:
                nested.append(item)
        return nested

    @classmethod
    def _extract_stream_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                item_type = str(cls._field(item, "type", "") or "").lower()
                if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
                    continue
                if item_type in {"tool_call", "function_call", "tool_use"}:
                    continue
                text = cls._field(item, "text")
                if text is not None:
                    parts.append(cls._extract_stream_text(text))
                    continue
                output_text = cls._field(item, "output_text")
                if output_text is not None:
                    parts.append(cls._extract_stream_text(output_text))
                    continue
                content = cls._field(item, "content")
                if content is not None:
                    parts.append(cls._extract_stream_text(content))
                    continue
                for nested in cls._nested_container_values(item):
                    parts.append(cls._extract_stream_text(nested))
            return "".join(parts)
        item_type = str(cls._field(value, "type", "") or "").lower()
        if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
            return ""
        if item_type in {"tool_call", "function_call", "tool_use"}:
            return ""
        text = cls._field(value, "text")
        if text is not None:
            return cls._extract_stream_text(text)
        output_text = cls._field(value, "output_text")
        if output_text is not None:
            return cls._extract_stream_text(output_text)
        content = cls._field(value, "content")
        if content is not None:
            return cls._extract_stream_text(content)
        parts = [cls._extract_stream_text(nested) for nested in cls._nested_container_values(value)]
        return "".join(parts)

    @classmethod
    def _extract_reasoning_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                item_type = str(cls._field(item, "type", "") or "").lower()
                if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
                    text = cls._extract_stream_text(cls._field(item, "text"))
                    if text:
                        parts.append(text)
                        continue
                    output_text = cls._extract_stream_text(cls._field(item, "output_text"))
                    if output_text:
                        parts.append(output_text)
                        continue
                    content = cls._extract_stream_text(cls._field(item, "content"))
                    if content:
                        parts.append(content)
                        continue
                for key in ("reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"):
                    nested = cls._field(item, key)
                    if nested is not None:
                        parts.append(cls._extract_reasoning_text(nested))
                for nested in cls._nested_container_values(item):
                    parts.append(cls._extract_reasoning_text(nested))
            return "".join(parts)
        parts: list[str] = []
        for key in ("reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"):
            nested = cls._field(value, key)
            if nested is not None:
                parts.append(cls._extract_reasoning_text(nested))
        if parts:
            return "".join(parts)
        item_type = str(cls._field(value, "type", "") or "").lower()
        if item_type in {"reasoning", "reasoning_content", "reasoning_text", "thinking", "thinking_text"}:
            text = cls._extract_stream_text(cls._field(value, "text"))
            if text:
                return text
            output_text = cls._extract_stream_text(cls._field(value, "output_text"))
            if output_text:
                return output_text
            return cls._extract_stream_text(cls._field(value, "content"))
        content = cls._field(value, "content")
        if isinstance(content, list):
            return cls._extract_reasoning_text(content)
        nested_parts = [cls._extract_reasoning_text(nested) for nested in cls._nested_container_values(value)]
        joined = "".join(nested_parts)
        if joined:
            return joined
        return ""

    @classmethod
    def _first_choice(cls, value: Any) -> Any:
        choices = cls._field(value, "choices", []) or []
        if isinstance(choices, list) and choices:
            return choices[0]
        return None

    @classmethod
    def _stream_payload(cls, chunk: Any) -> tuple[Any, str | None]:
        choice = cls._first_choice(chunk)
        if choice is not None:
            delta = cls._field(choice, "delta")
            finish_reason = cls._field(choice, "finish_reason")
            if delta is not None:
                return delta, str(finish_reason) if finish_reason else None
            return choice, str(finish_reason) if finish_reason else None
        finish_reason = cls._field(chunk, "finish_reason")
        if finish_reason is None:
            finish_reason = cls._field(chunk, "status")
        return chunk, str(finish_reason) if finish_reason else None

    @classmethod
    def _normalize_tool_call_entry(cls, value: Any, *, index: int = 0) -> dict[str, Any] | None:
        function = cls._field(value, "function")
        item_type = str(cls._field(value, "type", "") or "").lower()
        if function is None and item_type not in {"function_call", "tool_call", "tool_use", "function"}:
            return None

        name = cls._field(function, "name") if function is not None else None
        if name is None:
            name = cls._field(value, "name") or cls._field(value, "tool_name")

        arguments = cls._field(function, "arguments") if function is not None else None
        if arguments is None:
            arguments = cls._field(value, "arguments")
        if arguments is None:
            arguments = cls._field(value, "input")
        if isinstance(arguments, (dict, list)):
            arguments = json.dumps(arguments, ensure_ascii=False)

        return {
            "index": index,
            "id": cls._field(value, "id"),
            "type": "function",
            "function": {
                "name": str(name or ""),
                "arguments": str(arguments or ""),
            },
        }

    @classmethod
    def _extract_stream_tool_call_deltas(cls, value: Any) -> list[dict[str, Any]]:
        direct = cls._field(value, "tool_calls")
        if isinstance(direct, list) and direct:
            return list(direct)

        if isinstance(value, list):
            entries: list[dict[str, Any]] = []
            for index, item in enumerate(value):
                normalized = cls._normalize_tool_call_entry(item, index=index)
                if normalized is not None:
                    entries.append(normalized)
            if entries:
                return entries
            for item in value:
                nested = cls._extract_stream_tool_call_deltas(item)
                if nested:
                    return nested
            return []

        normalized = cls._normalize_tool_call_entry(value)
        if normalized is not None:
            return [normalized]

        for nested in cls._nested_container_values(value):
            tool_calls = cls._extract_stream_tool_call_deltas(nested)
            if tool_calls:
                return tool_calls
        return []

    @classmethod
    def _extract_tool_calls(cls, value: Any) -> list[Any]:
        direct = cls._field(value, "tool_calls")
        if isinstance(direct, list) and direct:
            return list(direct)
        normalized = cls._extract_stream_tool_call_deltas(value)
        if not normalized:
            return []
        return [
            {
                "id": str(item.get("id") or f"normalized-tool-{index}"),
                "type": str(item.get("type") or "function"),
                "function": {
                    "name": str(cls._field(item.get("function"), "name", "") or ""),
                    "arguments": str(cls._field(item.get("function"), "arguments", "") or ""),
                },
            }
            for index, item in enumerate(normalized, start=1)
        ]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg-{uuid.uuid4().hex[:12]}"

    @classmethod
    def _normalize_usage(cls, usage: Any) -> dict[str, int] | None:
        if usage is None:
            return None
        if isinstance(usage, dict):
            raw = usage
        else:
            raw = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        normalized: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = raw.get(key)
            if value is None:
                continue
            try:
                normalized[key] = int(value)
            except (TypeError, ValueError):
                continue
        return normalized or None

    @staticmethod
    def _auto_compact_threshold_tokens(max_context_tokens: int) -> int:
        threshold = int(max_context_tokens * 0.8)
        if threshold > 0:
            return threshold
        return max(max_context_tokens, 1)

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text.encode("utf-8")) + 3) // 4)

    @classmethod
    def _estimate_context_usage(cls, messages: list[dict[str, Any]]) -> dict[str, int]:
        total_tokens = 0
        for message in messages:
            total_tokens += 6
            for key in ("role", "content", "reasoning", "tool_call_id", "tool_name"):
                value = message.get(key)
                if value is None:
                    continue
                total_tokens += cls._estimate_text_tokens(str(value))
            tool_calls = message.get("tool_calls")
            if tool_calls:
                try:
                    tool_call_text = json.dumps(tool_calls, ensure_ascii=False)
                except TypeError:
                    tool_call_text = str(tool_calls)
                total_tokens += cls._estimate_text_tokens(tool_call_text)
        return {"total_tokens": max(total_tokens, 1)}

    @staticmethod
    def _latest_user_message_id(messages: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            message_id = message.get("message_id")
            if isinstance(message_id, str) and message_id.strip():
                return message_id
        return None

    @classmethod
    def _merge_stream_tool_call_delta(
        cls,
        tool_call_map: dict[int, dict[str, Any]],
        delta: Any,
    ) -> None:
        index_raw = cls._field(delta, "index", 0)
        try:
            index = int(index_raw)
        except (TypeError, ValueError):
            index = 0

        entry = tool_call_map.setdefault(
            index,
            {
                "id": None,
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        tc_id = cls._field(delta, "id")
        if tc_id:
            entry["id"] = str(tc_id)

        tc_type = cls._field(delta, "type")
        if tc_type:
            entry["type"] = str(tc_type)

        function = cls._field(delta, "function")
        if function is None:
            return

        tc_name = cls._field(function, "name")
        if tc_name:
            if entry["function"]["name"]:
                entry["function"]["name"] += str(tc_name)
            else:
                entry["function"]["name"] = str(tc_name)

        tc_args = cls._field(function, "arguments")
        if tc_args:
            entry["function"]["arguments"] += str(tc_args)

    @classmethod
    def _tool_call_id(cls, tool_call: Any) -> str:
        tc_id = cls._field(tool_call, "id")
        if tc_id:
            return str(tc_id)
        return ""

    @classmethod
    def _tool_call_name(cls, tool_call: Any) -> str:
        function = cls._field(tool_call, "function", {})
        tc_name = cls._field(function, "name", "")
        return str(tc_name or "")

    @classmethod
    def _tool_call_arguments(cls, tool_call: Any) -> str:
        function = cls._field(tool_call, "function", {})
        tc_args = cls._field(function, "arguments", "")
        return str(tc_args or "")

    @staticmethod
    async def _close_stream(stream: Any) -> None:
        close_fn = getattr(stream, "close", None)
        if callable(close_fn):
            result = close_fn()
            if hasattr(result, "__await__"):
                await result
            return

        aclose_fn = getattr(stream, "aclose", None)
        if callable(aclose_fn):
            result = aclose_fn()
            if hasattr(result, "__await__"):
                await result

    async def _create_message_stream_with_interrupt(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict] | None,
        max_tokens: int,
        assistant_message_id: str,
        parent_user_message_id: str | None,
        interrupt_queue: "asyncio.Queue[None] | None" = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, int] | None = None
        tool_call_map: dict[int, dict[str, Any]] = {}
        stream_line_open = False
        rendered_any = False
        pending_display_parts: list[str] = []
        assistant_started = False
        thinking_ctx = (
            status("[bright_black]Thinking…[/]")
            if self.status_enabled and self._ui_enabled()
            else nullcontext()
        )
        thinking_open = False
        stream: Any | None = None

        try:
            thinking_ctx.__enter__()
            thinking_open = True

            if interrupt_queue is None:
                stream = await self.client.create_message_stream_async(
                    messages=messages,
                    system=system,
                    tools=tools,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                )
            else:
                self._drain_interrupt_queue(interrupt_queue)
                llm_task = asyncio.create_task(
                    self.client.create_message_stream_async(
                        messages=messages,
                        system=system,
                        tools=tools,
                        max_tokens=max_tokens,
                        reasoning_effort=reasoning_effort,
                    )
                )
                interrupt_task = asyncio.create_task(interrupt_queue.get())
                done, _pending = await asyncio.wait(
                    {llm_task, interrupt_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if llm_task in done:
                    interrupt_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await interrupt_task
                    stream = await llm_task
                else:
                    llm_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await llm_task
                    with suppress(Exception):
                        interrupt_task.result()
                    raise UserInterruptedError("Interrupted by ESC")

            iterator = stream.__aiter__()
            while True:
                if interrupt_queue is not None:
                    try:
                        interrupt_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    else:
                        raise UserInterruptedError("Interrupted by ESC")

                try:
                    if interrupt_queue is None:
                        chunk = await iterator.__anext__()
                    else:
                        next_chunk_task = asyncio.create_task(iterator.__anext__())
                        while True:
                            done, _pending = await asyncio.wait(
                                {next_chunk_task},
                                timeout=0.1,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if next_chunk_task in done:
                                break
                            try:
                                interrupt_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            else:
                                next_chunk_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await next_chunk_task
                                raise UserInterruptedError("Interrupted by ESC")
                        chunk = await next_chunk_task
                except StopAsyncIteration:
                    break

                delta, chunk_finish = self._stream_payload(chunk)

                if delta is not None:
                    reasoning_text = self._extract_reasoning_text(delta)
                    if reasoning_text:
                        reasoning_parts.append(reasoning_text)
                        if not assistant_started:
                            assistant_started = True
                            await self._emit(
                                {
                                    "type": "assistant_start",
                                    "message_id": assistant_message_id,
                                    "parent_user_message_id": parent_user_message_id,
                                }
                            )
                        await self._emit(
                            {
                                "type": "assistant_reasoning_delta",
                                "message_id": assistant_message_id,
                                "parent_user_message_id": parent_user_message_id,
                                "reasoning_text": reasoning_text,
                            }
                        )

                    chunk_text = self._extract_stream_text(delta)
                    if chunk_text:
                        text_parts.append(chunk_text)
                        if not assistant_started:
                            assistant_started = True
                            await self._emit(
                                {
                                    "type": "assistant_start",
                                    "message_id": assistant_message_id,
                                    "parent_user_message_id": parent_user_message_id,
                                }
                            )
                        await self._emit(
                            {
                                "type": "assistant_delta",
                                "message_id": assistant_message_id,
                                "parent_user_message_id": parent_user_message_id,
                                "delta_text": chunk_text,
                            }
                        )
                        if self._ui_enabled():
                            if not rendered_any:
                                pending_display_parts.append(chunk_text)
                                candidate = "".join(pending_display_parts)
                                if candidate.strip():
                                    if thinking_open:
                                        thinking_ctx.__exit__(None, None, None)
                                        thinking_open = False
                                    stream_assistant_start(title=self._assistant_title())
                                    stream_line_open = True
                                    stream_assistant_write(candidate)
                                    rendered_any = True
                                    pending_display_parts = []
                            else:
                                stream_assistant_write(chunk_text)

                    tool_call_deltas = self._extract_stream_tool_call_deltas(delta)
                    for tool_call_delta in tool_call_deltas:
                        self._merge_stream_tool_call_delta(tool_call_map, tool_call_delta)

                if chunk_finish:
                    finish_reason = str(chunk_finish)

                usage = self._normalize_usage(self._field(chunk, "usage")) or usage
        finally:
            if thinking_open:
                with suppress(Exception):
                    thinking_ctx.__exit__(None, None, None)
            if stream_line_open:
                stream_assistant_end()
            if stream is not None:
                with suppress(Exception):
                    await self._close_stream(stream)

        tool_calls: list[dict[str, Any]] = []
        for index, entry in sorted(tool_call_map.items(), key=lambda item: item[0]):
            tc_id = entry.get("id")
            if not tc_id:
                entry["id"] = f"stream-call-{index}"
            if not entry["function"]["name"]:
                entry["function"]["name"] = "unknown_tool"
            tool_calls.append(entry)

        return {
            "content": "".join(text_parts),
            "reasoning": "".join(reasoning_parts),
            "finish_reason": finish_reason or "stop",
            "tool_calls": tool_calls,
            "stream_rendered": rendered_any and not self.silent,
            "usage": usage,
        }

    async def _create_message_with_interrupt(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict] | None,
        max_tokens: int,
        interrupt_queue: "asyncio.Queue[None] | None" = None,
        reasoning_effort: str | None = None,
    ) -> Any:
        if interrupt_queue is None:
            return await self.client.create_message_async(
                messages=messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            )

        self._drain_interrupt_queue(interrupt_queue)
        llm_task = asyncio.create_task(
            self.client.create_message_async(
                messages=messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            )
        )
        interrupt_task = asyncio.create_task(interrupt_queue.get())

        done, _pending = await asyncio.wait(
            {llm_task, interrupt_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if llm_task in done:
            interrupt_task.cancel()
            with suppress(asyncio.CancelledError):
                await interrupt_task
            return await llm_task

        llm_task.cancel()
        with suppress(asyncio.CancelledError):
            await llm_task
        with suppress(Exception):
            interrupt_task.result()
        raise UserInterruptedError("Interrupted by ESC")

    async def run_loop(
        self,
        messages: list[dict],
        *,
        interrupt_queue: "asyncio.Queue[None] | None" = None,
        reasoning_effort: str | None = None,
    ) -> list[dict]:
        """Run the main agent loop."""
        while True:
            tools = self.tool_registry.get_definitions()
            content = ""
            reasoning = ""
            finish_reason = "stop"
            tool_calls: list[Any] = []
            stream_rendered = False
            stream_success = False
            compaction_context_usage: dict[str, int] | None = None

            usage = None
            assistant_message_id = self._new_message_id()
            parent_user_message_id = self._latest_user_message_id(messages)
            max_context_tokens = int(self.config.llm.max_context_tokens)
            auto_compact_threshold_tokens = self._auto_compact_threshold_tokens(max_context_tokens)

            if self._streaming_active():
                try:
                    stream_result = await self._create_message_stream_with_interrupt(
                        messages=messages,
                        system=self.system_prompt,
                        tools=tools,
                        max_tokens=self.config.llm.max_tokens,
                        assistant_message_id=assistant_message_id,
                        parent_user_message_id=parent_user_message_id,
                        interrupt_queue=interrupt_queue,
                        reasoning_effort=reasoning_effort,
                    )
                    content = stream_result["content"]
                    reasoning = stream_result.get("reasoning", "")
                    finish_reason = stream_result["finish_reason"]
                    tool_calls = stream_result["tool_calls"]
                    stream_rendered = bool(stream_result["stream_rendered"])
                    usage = stream_result.get("usage")
                    stream_success = True
                except UserInterruptedError:
                    raise
                except Exception as exc:
                    self.stream_degraded = True
                    await self._emit(
                        {
                            "type": "system",
                            "message": "Streaming unavailable, fell back to non-streaming.",
                            "data": {"error": str(exc)},
                        }
                    )
                    if self._ui_enabled():
                        print_system(f"Streaming unavailable, fell back to non-streaming: {exc}")

            if not stream_success:
                status_ctx = (
                    status("[bright_black]Thinking…[/]")
                    if self.status_enabled and self._ui_enabled()
                    else nullcontext()
                )
                with status_ctx:
                    response = await self._create_message_with_interrupt(
                        messages=messages,
                        system=self.system_prompt,
                        tools=tools,
                        max_tokens=self.config.llm.max_tokens,
                        interrupt_queue=interrupt_queue,
                        reasoning_effort=reasoning_effort,
                    )

                choice = self._first_choice(response)
                assistant_message = self._field(choice, "message") if choice is not None else None
                payload = assistant_message if assistant_message is not None else response
                finish_reason_raw = self._field(choice, "finish_reason") if choice is not None else None
                if finish_reason_raw is None:
                    finish_reason_raw = self._field(response, "finish_reason") or self._field(response, "status")
                finish_reason = str(finish_reason_raw or "stop")
                content = self._extract_stream_text(payload)
                reasoning = self._extract_reasoning_text(payload)
                tool_calls = self._extract_tool_calls(payload)

                usage = self._normalize_usage(self._field(response, "usage"))

            if usage:
                await self._emit(
                    {
                        "type": "system",
                        "data": {
                            "usage": usage,
                            "max_context_tokens": max_context_tokens,
                            "auto_compact_threshold_tokens": auto_compact_threshold_tokens,
                        },
                    }
                )
                total_tokens = usage.get("total_tokens", 0)
                if self._ui_enabled():
                    print_system(f"\\[Context: {total_tokens}/{max_context_tokens}]")
                
                if total_tokens > auto_compact_threshold_tokens:
                    await self._emit(
                        {
                            "type": "system",
                            "message": "Context length exceeds 80%. Running automatic compaction...",
                            "data": {
                                "total_tokens": total_tokens,
                                "threshold": auto_compact_threshold_tokens,
                                "max_context_tokens": max_context_tokens,
                                "auto_compact_threshold_tokens": auto_compact_threshold_tokens,
                            },
                        }
                    )
                    if self._ui_enabled():
                        print_system(f"Context length exceeds 80% ({total_tokens} > {auto_compact_threshold_tokens}). "
                                     f"Running automatic compaction...")
                    
                    compaction_system = (
                        "You are an internal system agent responsible for summarizing long conversation histories. "
                        "Summarize the conversation strictly in markdown format. "
                        "Include the following details clearly: "
                        "1. What happened previously.\n"
                        "2. The user's goal/purpose.\n"
                        "3. What tasks exist and their current status/progress.\n"
                        "Be detailed but concise enough to replace the previous message history."
                    )
                    
                    try:
                        status_ctx = (
                            status("[bright_black]Compacting context…[/]")
                            if self.status_enabled and self._ui_enabled()
                            else nullcontext()
                        )
                        with status_ctx:
                            compaction_resp = await self._create_message_with_interrupt(
                                messages=messages,
                                system=compaction_system,
                                tools=None,
                                max_tokens=self.config.llm.max_tokens,
                                interrupt_queue=interrupt_queue,
                            )
                        compaction_choice = self._first_choice(compaction_resp)
                        compaction_payload = (
                            self._field(compaction_choice, "message")
                            if compaction_choice is not None
                            else compaction_resp
                        )
                        summary = self._extract_stream_text(compaction_payload) or "Failed to summarize context."
                        
                        # Replace history with compaction message, keeping only the final assistant/tool calls logic intact.
                        compaction_msg = {
                            "role": "assistant",
                            "content": f"**[SYSTEM: Context Compacted]**\n\n{summary}",
                            "is_compaction": True,
                            "message_id": self._new_message_id(),
                        }
                        assistant_preview: dict[str, Any] = {
                            "role": "assistant",
                            "content": content,
                        }
                        if reasoning:
                            assistant_preview["reasoning"] = reasoning
                        if tool_calls:
                            assistant_preview["tool_calls"] = [
                                {
                                    "id": self._tool_call_id(tc) or f"tool-call-{i}",
                                    "type": "function",
                                    "function": {
                                        "name": self._tool_call_name(tc),
                                        "arguments": self._tool_call_arguments(tc),
                                    },
                                }
                                for i, tc in enumerate(tool_calls, start=1)
                            ]
                        compaction_context_usage = self._estimate_context_usage([compaction_msg, assistant_preview])
                        compaction_msg["context_usage"] = dict(compaction_context_usage)
                        # We clear the existing message list safely and append the summary
                        messages.clear()
                        messages.append(compaction_msg)
                        
                        await self._emit(
                            {
                                "type": "system",
                                "message": "Context compaction completed. Older history dropped.",
                                "data": {
                                    "context_compacted": True,
                                    "context_usage": compaction_context_usage,
                                    "max_context_tokens": max_context_tokens,
                                    "auto_compact_threshold_tokens": auto_compact_threshold_tokens,
                                },
                            }
                        )
                        if self._ui_enabled():
                            print_system("Context compaction completed. Older history dropped.")
                            
                    except Exception as e:
                        await self._emit(
                            {
                                "type": "system",
                                "message": "Warning: Context compaction failed.",
                                "data": {"error": str(e)},
                            }
                        )
                        if self._ui_enabled():
                            print_system(f"Warning: Context compaction failed: {e}")

            # Emit structured assistant end event for all non-interrupted responses.
            tool_call_summaries = [
                {
                    "id": self._tool_call_id(tc) or f"tool-call-{i}",
                    "name": self._tool_call_name(tc),
                    "arguments": self._tool_call_arguments(tc),
                }
                for i, tc in enumerate(tool_calls, start=1)
            ]
            await self._emit(
                {
                    "type": "assistant_end",
                    "message_id": assistant_message_id,
                    "parent_user_message_id": parent_user_message_id,
                    "content": content,
                    "reasoning": reasoning,
                    "finish_reason": finish_reason,
                    "tool_calls": tool_call_summaries,
                    "usage": usage or {},
                }
            )

            if content and self._ui_enabled() and not stream_rendered:
                print_assistant(content, title=self._assistant_title())

            if finish_reason != "tool_calls":
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                    "message_id": assistant_message_id,
                    "parent_user_message_id": parent_user_message_id,
                }
                if reasoning:
                    assistant_msg["reasoning"] = reasoning
                if usage:
                    assistant_msg["usage"] = usage
                if compaction_context_usage:
                    assistant_msg["context_usage"] = dict(compaction_context_usage)
                messages.append(assistant_msg)
                return messages

            tool_results: list[dict[str, str]] = []
            for i, tc in enumerate(tool_calls, start=1):
                tc_id = self._tool_call_id(tc) or f"tool-call-{i}"
                tc_name = self._tool_call_name(tc)
                tc_args_raw = self._tool_call_arguments(tc)
                tc_input, parse_note = parse_tool_arguments(tc_args_raw)
                if tc_input is None:
                    output = (
                        f"Error: Invalid JSON arguments for tool '{tc_name}': {parse_note}. "
                        f"raw={tc_args_raw!r}"
                    )[:50000]
                    await self._emit(
                        {
                            "type": "tool_call",
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "tool_args": {},
                            "is_error": True,
                            "note": parse_note or "invalid_args",
                        }
                    )
                    await self._emit(
                        {
                            "type": "tool_result",
                            "tool_call_id": tc_id,
                            "tool_name": tc_name,
                            "tool_output": output,
                            "is_error": True,
                        }
                    )
                    if self._ui_enabled():
                        print()
                        print_tool_call(f"{self._tool_log_prefix()}> {tc_name}(invalid_args)")
                        print_tool_output(self._tool_output_name(tc_name), output)
                    tool_results.append({"tool_call_id": tc_id, "output": output})
                    continue

                await self._emit(
                    {
                        "type": "tool_call",
                        "tool_call_id": tc_id,
                        "tool_name": tc_name,
                        "tool_args": tc_input,
                        "note": parse_note or "",
                    }
                )

                if self._ui_enabled():
                    if tc_name == "Task":
                        print()
                        print_tool_call(
                            f"{self._tool_log_prefix()}> Task: {tc_input.get('description', 'subtask')}"
                        )
                    elif tc_name == "Skill":
                        print()
                        print_tool_call(
                            f"{self._tool_log_prefix()}> Loading skill: {tc_input.get('skill', '?')}"
                        )
                    else:
                        args_preview = _format_tool_args(tc_input)
                        print()
                        print_tool_call(f"{self._tool_log_prefix()}> {tc_name}({args_preview})")

                if tc_name == "Task":
                    output = await self._execute_tool_with_hooks(tc_name, tc_input)
                else:
                    tool_status_ctx = (
                        status(f"[bright_black]Running {tc_name}…[/]")
                        if self.status_enabled and self._ui_enabled()
                        else nullcontext()
                    )
                    with tool_status_ctx:
                        output = await self._execute_tool_with_hooks(tc_name, tc_input)

                if parse_note:
                    output = f"Warning: {parse_note}\n{output}"
                await self._emit(
                    {
                        "type": "tool_result",
                        "tool_call_id": tc_id,
                        "tool_name": tc_name,
                        "tool_output": output,
                        "is_error": False,
                        "note": parse_note or "",
                    }
                )
                if self._ui_enabled():
                    print_tool_output(self._tool_output_name(tc_name), output)
                tool_results.append({"tool_call_id": tc_id, "output": output})

            assistant_msg = {
                "role": "assistant",
                "content": content,
                "message_id": assistant_message_id,
                "parent_user_message_id": parent_user_message_id,
            }
            if reasoning:
                assistant_msg["reasoning"] = reasoning
            if usage:
                assistant_msg["usage"] = usage
            if compaction_context_usage:
                assistant_msg["context_usage"] = dict(compaction_context_usage)
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": self._tool_call_id(tc) or f"tool-call-{i}",
                        "type": "function",
                        "function": {
                            "name": self._tool_call_name(tc),
                            "arguments": self._tool_call_arguments(tc),
                        },
                    }
                    for i, tc in enumerate(tool_calls, start=1)
                ]
            messages.append(assistant_msg)

            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "message_id": self._new_message_id(),
                        "parent_user_message_id": parent_user_message_id,
                        "tool_call_id": result["tool_call_id"],
                        "content": result["output"],
                    }
                )

    async def start_session(self) -> None:
        """Start the agent session."""
        await self.hook_manager.trigger_session_start(self.session_id)

    async def end_session(self) -> None:
        """End the agent session."""
        await self.hook_manager.trigger_session_end(self.session_id)
        if self.role in {"solo", "lead"} and self._owns_team_runtime and self.team_runtime:
            try:
                await self.team_runtime.cleanup_team(actor_role="lead")
            except Exception:
                pass
        try:
            await self.disconnect_mcp_servers()
        finally:
            await self.close_client()

    def get_info(self) -> dict[str, Any]:
        """Get agent information."""
        team_info: dict[str, Any] = {"active": False}
        if self.team_runtime and self.team_runtime.store.team is not None:
            team = self.team_runtime.store.team
            members = list(team.members.values())
            team_info = {
                "active": True,
                "team_id": team.team_id,
                "name": team.name,
                "member_count": len(members),
                "running_members": sum(1 for m in members if m.status.value == "in_progress"),
            }
        return {
            "workdir": str(self.workdir),
            "model": self.client.model,
            "session_id": self.session_id,
            "role": self.role,
            "team_id": self.team_id,
            "member_id": self.member_id,
            "skills": self.skill_loader.list_skills(),
            "agent_types": self.agent_registry.list_names(),
            "tools": [t.name for t in self.tool_registry.get_all()],
            "mcp_servers": self.mcp_manager.list_servers(),
            "mcp_tools": self.mcp_manager.tool_count,
            "hooks_enabled": self.hook_manager.enabled,
            "team": team_info,
            "stream": self.get_stream_state(),
        }
