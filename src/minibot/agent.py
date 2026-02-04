"""Main Agent implementation"""

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
)
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
            # Truncate long strings
            if len(value) > 30:
                value = value[:27] + "..."
            parts.append(f'{key}="{value}"')
        elif isinstance(value, bool):
            parts.append(f"{key}={value}")
        elif isinstance(value, (int, float)):
            parts.append(f"{key}={value}")
        else:
            # For complex types, just show type
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

        # Initialize components
        self.client = LLMClient(
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key,
            model=self.config.llm.model,
        )

        self.skill_loader = SkillLoader(self.config.skills_dir)
        self.todo_manager = TodoManager()
        self.agent_registry = AgentRegistry()
        self.tool_registry = ToolRegistry()

        # Initialize hooks manager
        self.hook_manager = HookManager(self.config.hooks, self.workdir)

        # Initialize MCP manager
        self.mcp_manager = MCPManager(self.config.mcp, self.workdir)

        # Initialize subagent executor
        self.subagent_executor = SubagentExecutor(
            client=self.client,
            tool_registry=self.tool_registry,
            agent_registry=self.agent_registry,
            hook_manager=self.hook_manager,
            workdir=self.workdir,
        )

        # Register tools
        self._register_tools()

        # Build system prompt
        self.system_prompt = self._build_system_prompt()

    def _register_tools(self) -> None:
        """Register all available tools."""
        # Built-in tools
        self.tool_registry.register(
            BashTool(self.workdir, self.config.tools.timeout)
        )
        self.tool_registry.register(ReadFileTool(self.workdir))
        self.tool_registry.register(WriteFileTool(self.workdir))
        self.tool_registry.register(EditFileTool(self.workdir))
        self.tool_registry.register(TodoWriteTool(self.todo_manager))

        # Meta tools
        self.tool_registry.register(SkillTool(self.skill_loader))
        self.tool_registry.register(
            TaskTool(self.agent_registry, self.subagent_executor)
        )

    async def connect_mcp_servers(self) -> dict[str, Exception | None]:
        """Connect to all configured MCP servers."""
        errors = await self.mcp_manager.connect_all()

        # Register MCP tools
        tool_count = self.mcp_manager.register_tools(self.tool_registry)
        if tool_count > 0:
            # Rebuild system prompt to include MCP tools info
            self.system_prompt = self._build_system_prompt()

        return errors

    async def disconnect_mcp_servers(self) -> None:
        """Disconnect from all MCP servers."""
        await self.mcp_manager.disconnect_all()

    def _build_system_prompt(self) -> str:
        """Build the system prompt."""
        mcp_info = ""
        if self.mcp_manager.server_count > 0:
            mcp_info = f"""

**MCP Servers connected:** {', '.join(self.mcp_manager.list_servers())}
MCP tools are prefixed with `mcp__<server>__<tool>`."""

        return f"""You are a coding agent at {self.workdir}.

Loop: plan -> act with tools -> report.

**Skills available** (invoke with Skill tool when task matches):
{self.skill_loader.get_descriptions()}

**Subagents available** (invoke with Task tool for focused subtasks):
{self.agent_registry.get_descriptions()}{mcp_info}

Rules:
- Use Skill tool IMMEDIATELY when a task matches a skill description
- Use Task tool for subtasks needing focused exploration or implementation
- Use TodoWrite to track multi-step work
- Prefer tools over prose. Act, don't just explain.
- After finishing, summarize what changed."""

    async def _execute_tool_with_hooks(
        self,
        name: str,
        args: dict[str, Any],
    ) -> str:
        """Execute a tool with pre/post hooks."""
        # Pre-tool hook
        blocked, reason = await self.hook_manager.trigger_pre_tool_call(name, args)
        if blocked:
            return f"Blocked by hook: {reason}"

        # Execute tool
        result = await self.tool_registry.execute(name, args)

        # Post-tool hook
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

            # OpenAI API 响应格式
            choice = response.choices[0]
            assistant_message = choice.message
            finish_reason = choice.finish_reason
            content = assistant_message.content or ""

            # 打印 assistant 的文本内容
            if content:
                print_assistant(content)

            # 处理 tool calls (OpenAI 格式)
            tool_calls = assistant_message.tool_calls or []

            if finish_reason != "tool_calls":
                messages.append({"role": "assistant", "content": content})
                return messages

            tool_results: list[dict[str, str]] = []
            for tc in tool_calls:
                tc_id = tc.id
                tc_name = tc.function.name
                # tc.function.arguments 是 JSON 字符串，需要解析为字典
                tc_input = json.loads(tc.function.arguments)

                # Display tool execution
                if tc_name == "Task":
                    print()
                    print_tool_call(f"> Task: {tc_input.get('description', 'subtask')}")
                elif tc_name == "Skill":
                    print()
                    print_tool_call(f"> Loading skill: {tc_input.get('skill', '?')}")
                else:
                    # Format arguments for display
                    args_preview = _format_tool_args(tc_input)
                    print()
                    print_tool_call(f"> {tc_name}({args_preview})")

                # NOTE: Task tool runs subagents which use Rich Live rendering for progress.
                # Nesting console.status (also Live-based) would disable subagent Live and
                # cause a fallback to the old \r-updating line.
                if tc_name == "Task":
                    output = await self._execute_tool_with_hooks(tc_name, tc_input)
                else:
                    with status(f"[bright_black]Running {tc_name}…[/]"):
                        output = await self._execute_tool_with_hooks(tc_name, tc_input)
                print_tool_output(tc_name, output)
                tool_results.append({"tool_call_id": tc_id, "output": output})

            # OpenAI API: 构建 assistant 消息（包含 tool_calls）
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

            # OpenAI API: tool results use role="tool" with tool_call_id
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
