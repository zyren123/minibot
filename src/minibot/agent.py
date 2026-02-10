"""Main Agent implementation"""

from datetime import datetime
import json
import uuid
from typing import Any

from .config import Config, get_config
from .core.client import LLMClient
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
from .tools.meta import TaskTool, SkillTool
from .skills.loader import SkillLoader
from .subagents.registry import AgentRegistry
from .subagents.executor import SubagentExecutor
from .hooks.manager import HookManager
from .mcp.manager import MCPManager
from .utils.output import print_assistant, print_tool_call, print_tool_output, print_system, status


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
        result = result[:max_len - 3] + "..."
    return result


class Agent:
    """Main agent with tools, skills, hooks, and MCP support."""

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.workdir = self.config.workdir
        self.session_id = str(uuid.uuid4())[:8]

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
            MemoryManager(self.config.memory, self.workdir)
            if self.config.memory.enabled
            else None
        )

        self.subagent_executor = SubagentExecutor(
            client=self.client,
            tool_registry=self.tool_registry,
            agent_registry=self.agent_registry,
            hook_manager=self.hook_manager,
            workdir=self.workdir,
        )

        self._register_tools()

        self.system_prompt = self._build_system_prompt()

    def _register_tools(self) -> None:
        """Register all available tools."""
        self.tool_registry.register(
            BashTool(self.workdir, self.config.tools.timeout)
        )
        self.tool_registry.register(ReadFileTool(self.workdir))
        self.tool_registry.register(WriteFileTool(self.workdir))
        self.tool_registry.register(EditFileTool(self.workdir))
        self.tool_registry.register(TodoWriteTool(self.todo_manager))

        self.tool_registry.register(SkillTool(self.skill_loader))
        self.tool_registry.register(
            TaskTool(self.agent_registry, self.subagent_executor)
        )

        if self.memory_manager is not None:
            self.tool_registry.register(MemoryReadTool(self.memory_manager))
            self.tool_registry.register(MemoryWriteTool(self.memory_manager))

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

    def _build_system_prompt(self) -> str:
        """Build the system prompt following Professional ReAct standards."""

        mcp_section = ""
        if self.mcp_manager.server_count > 0:
            server_list = ', '.join(self.mcp_manager.list_servers())
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

        return f"""You are an advanced autonomous Coding Agent operating in `{self.workdir}`. your name is Minibot.
Your goal is to solve complex software engineering tasks by following a strict ReAct (Reason -> Act -> Observe) loop.

## Environment Context
- Working Directory: {self.workdir}
- User: Current active developer
- Date: {datetime.now().strftime("%Y-%m-%d")}

## Tool Capability Hierarchy
You have access to three layers of capabilities. Choose the most specific tool for the job:

1. **Core Skills** (High Precision):
   {self.skill_loader.get_descriptions()}
   *Rule: If a user request matches a Skill description, prefer the Skill tool over manual steps.*

2. **Specialized Subagents** (Delegation):
   {self.agent_registry.get_descriptions()}
   *Rule: Use the `Task` tool to delegate broad, ambiguous, or multi-step sub-problems to these agents.*
   *Agent Selection Guidelines:*
   - Use `explore` agent when user requests involve: exploring, analyzing, searching, reading, understanding, or discovering code/files
   - Use `plan` agent when user requests involve: planning, designing, strategizing, or outlining implementation steps
   - Use `code` agent when user requests involve: implementing, coding, fixing, modifying, or creating code/files

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

        result = await self.tool_registry.execute(name, args)

        await self.hook_manager.trigger_post_tool_call(name, args, result)

        return result

    async def run_loop(self, messages: list[dict]) -> list[dict]:
        """Run the main agent loop."""
        while True:
            with status("[bright_black]Thinking…[/]"):
                response = self.client.create_message(
                    messages=messages,
                    system=self.system_prompt,
                    tools=self.tool_registry.get_definitions(),
                    max_tokens=self.config.llm.max_tokens,
                )

            choice = response.choices[0]
            assistant_message = choice.message
            finish_reason = choice.finish_reason
            content = assistant_message.content or ""

            if content:
                print_assistant(content)

            tool_calls = assistant_message.tool_calls or []

            if finish_reason != "tool_calls":
                messages.append({"role": "assistant", "content": content})
                return messages

            tool_results: list[dict[str, str]] = []
            for tc in tool_calls:
                tc_id = tc.id
                tc_name = tc.function.name
                tc_input = json.loads(tc.function.arguments)

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
                    with status(f"[bright_black]Running {tc_name}…[/]"):
                        output = await self._execute_tool_with_hooks(tc_name, tc_input)
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
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            for r in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": r["tool_call_id"],
                        "content": r["output"],
                    }
                )

    async def start_session(self) -> None:
        """Start the agent session."""
        await self.hook_manager.trigger_session_start(self.session_id)

    async def end_session(self) -> None:
        """End the agent session."""
        await self.hook_manager.trigger_session_end(self.session_id)
        await self.disconnect_mcp_servers()

    def get_info(self) -> dict[str, Any]:
        """Get agent information."""
        return {
            "workdir": str(self.workdir),
            "model": self.client.model,
            "session_id": self.session_id,
            "skills": self.skill_loader.list_skills(),
            "agent_types": self.agent_registry.list_names(),
            "tools": [t.name for t in self.tool_registry.get_all()],
            "mcp_servers": self.mcp_manager.list_servers(),
            "mcp_tools": self.mcp_manager.tool_count,
            "hooks_enabled": self.hook_manager.enabled,
        }
