"""Main Agent implementation."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
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
from .utils.output import (
    print_assistant,
    print_tool_call,
    print_tool_output,
    print_system,
    status,
)

AgentRole = Literal["solo", "lead", "teammate"]


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
        team_runtime: TeamRuntime | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
    ):
        if role not in {"solo", "lead", "teammate"}:
            raise ValueError(f"Unknown role: {role}")

        self.config = config or get_config()
        self.workdir = self.config.workdir
        self.session_id = str(uuid.uuid4())[:8]
        self.role: AgentRole = role
        self.team_role: Literal["lead", "teammate"] = "teammate" if role == "teammate" else "lead"
        self.team_id = team_id
        self.member_id = member_id
        self.quiet_teammates = self.config.teams.quiet_teammates
        self.silent = role == "teammate" and self.quiet_teammates
        self.status_enabled = not (role == "teammate" and self.quiet_teammates)

        self.client = LLMClient(
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key,
            model=self.config.llm.model,
        )

        self.skill_loader = SkillLoader(self.config.skills_dir)
        self.todo_manager = TodoManager()
        self.agent_registry = AgentRegistry()
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
                print_system(f"Warning: {notice}")

        self.team_runtime: TeamRuntime | None = team_runtime
        self._owns_team_runtime = False
        if self.config.teams.enabled and self.role in {"solo", "lead"}:
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
        )

        self._register_tools()
        self.system_prompt = self._build_system_prompt()

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
        if self.role != "teammate":
            self.tool_registry.register(TaskTool(self.agent_registry, self.subagent_executor))

        if self.memory_manager is not None:
            self.tool_registry.register(MemoryReadTool(self.memory_manager))
            self.tool_registry.register(MemoryWriteTool(self.memory_manager))

        if self.config.teams.enabled and self.team_runtime is not None:
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
            self.system_prompt = self._build_system_prompt()

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
        if self.role != "teammate":
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
        if self.role in {"solo", "lead"} and self.config.teams.enabled:
            team_section = """
## Team Orchestration Policy
- You can decide whether to create a team (`TeamCreate`) and how many teammates to spawn.
- Prefer teams for parallelizable work: cross-module changes, competing debugging hypotheses, or multi-perspective reviews.
- Prefer solo execution for short sequential edits or same-file heavy changes.
- Typical teammate count is 2-6. Start small unless clear parallelism exists.
- Use `TeamTask`, `TeamMessage`, `TeamBroadcast`, and `TeamWait` to coordinate and synthesize results.
"""
        elif self.role == "teammate":
            team_section = f"""
## Team Role Constraints
- You are teammate `{self.member_id}` in team `{self.team_id}`.
- You MUST NOT create teammates or delegate with `Task`.
- Coordinate via `TeamMessage`, `TeamBroadcast`, and `TeamTask`.
- Keep updates concise and unblock other teammates quickly.
"""

        return f"""You are an advanced autonomous Coding Agent operating in `{self.workdir}`. your name is Minibot.
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

1. **Analyze**: Understand the current state. Read files or list directories if you lack context.
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

    async def run_loop(self, messages: list[dict]) -> list[dict]:
        """Run the main agent loop."""
        while True:
            status_ctx = (
                status("[bright_black]Thinking…[/]")
                if self.status_enabled
                else nullcontext()
            )
            with status_ctx:
                response = await self.client.create_message_async(
                    messages=messages,
                    system=self.system_prompt,
                    tools=self.tool_registry.get_definitions(),
                    max_tokens=self.config.llm.max_tokens,
                )

            choice = response.choices[0]
            assistant_message = choice.message
            finish_reason = choice.finish_reason
            content = assistant_message.content or ""

            if content and not self.silent:
                print_assistant(content)

            tool_calls = assistant_message.tool_calls or []

            if finish_reason != "tool_calls":
                messages.append({"role": "assistant", "content": content})
                return messages

            tool_results: list[dict[str, str]] = []
            for tc in tool_calls:
                tc_id = tc.id
                tc_name = tc.function.name
                tc_input, parse_note = parse_tool_arguments(tc.function.arguments)
                if tc_input is None:
                    output = (
                        f"Error: Invalid JSON arguments for tool '{tc_name}': {parse_note}. "
                        f"raw={tc.function.arguments!r}"
                    )[:50000]
                    if not self.silent:
                        print()
                        print_tool_call(f"> {tc_name}(invalid_args)")
                        print_tool_output(tc_name, output)
                    tool_results.append({"tool_call_id": tc_id, "output": output})
                    continue

                if not self.silent:
                    if tc_name == "Task":
                        print()
                        print_tool_call(f"> Task: {tc_input.get('description', 'subtask')}")
                    elif tc_name == "Skill":
                        print()
                        print_tool_call(f"> Loading skill: {tc_input.get('skill', '?')}")
                    else:
                        args_preview = _format_tool_args(tc_input)
                        print()
                        print_tool_call(f"> {tc_name}({args_preview})")

                if tc_name == "Task":
                    output = await self._execute_tool_with_hooks(tc_name, tc_input)
                else:
                    tool_status_ctx = (
                        status(f"[bright_black]Running {tc_name}…[/]")
                        if self.status_enabled
                        else nullcontext()
                    )
                    with tool_status_ctx:
                        output = await self._execute_tool_with_hooks(tc_name, tc_input)

                if parse_note:
                    output = f"Warning: {parse_note}\n{output}"
                if not self.silent:
                    print_tool_output(tc_name, output)
                tool_results.append({"tool_call_id": tc_id, "output": output})

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
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
        }
