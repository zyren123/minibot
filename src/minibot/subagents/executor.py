"""Subagent executor."""

import sys
import time
from pathlib import Path
from typing import Any

from ..core.client import LLMClient
from ..core.tool_args import parse_tool_arguments
from ..tools.registry import ToolRegistry
from ..hooks.manager import HookManager
from .registry import AgentRegistry
from ..utils.rich_utils import get_console, rich_enabled

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..skills.loader import SkillLoader


class SubagentExecutor:
    """Executes subagent tasks."""

    def __init__(
        self,
        client: LLMClient,
        tool_registry: ToolRegistry,
        agent_registry: AgentRegistry,
        hook_manager: HookManager,
        workdir: Path,
        skill_loader: "SkillLoader | None" = None,
    ):
        self.client = client
        self.tool_registry = tool_registry
        self.agent_registry = agent_registry
        self.hook_manager = hook_manager
        self.workdir = workdir
        self.skill_loader = skill_loader

    def _get_tools_for_agent(self, agent_type: str) -> list[dict]:
        """Get tool definitions for an agent type."""
        agent = self.agent_registry.get(agent_type)
        if agent is None:
            return []

        excluded = {"Task", "TeamCreate", "TeamMembers", "TeamTask", "TeamMessage", "TeamBroadcast", "TeamWait", "TeamShutdown"}
        if not agent.skills_enabled:
            excluded.add("Skill")

        if agent.tools == "*":
            return [
                t.to_dict()
                for t in self.tool_registry.get_all()
                if t.name not in excluded
            ]

        allowed_names = set(agent.tools)
        if agent.skills_enabled:
            allowed_names.add("Skill")

        return [
            t.to_dict()
            for t in self.tool_registry.get_all()
            if t.name in allowed_names
        ]

    async def execute(
        self,
        description: str,
        prompt: str,
        agent_type: str,
    ) -> str:
        """Execute a subagent task."""
        agent = self.agent_registry.get(agent_type)
        if agent is None:
            return f"Error: Unknown agent type '{agent_type}'"
        if not agent.enabled:
            return f"Error: Subagent '{agent_type}' is disabled"
        if agent.handler is not None:
            return await agent.handler(description, prompt, agent)

        skills_section = ""
        if agent.skills_enabled and self.skill_loader:
            skills_desc = self.skill_loader.get_descriptions()
            skills_section = f"\n\nAvailable Skills:\n{skills_desc}\nUse the Skill tool to load specialized knowledge when needed."

        sub_system = f"""You are a {agent_type} subagent at {self.workdir}.

{agent.prompt}{skills_section}

Complete the task and return a clear, concise summary."""

        sub_tools = self._get_tools_for_agent(agent_type)
        sub_messages = [{"role": "user", "content": prompt}]

        start = time.time()
        tool_count = 0
        content = ""

        use_rich = rich_enabled()
        live = None
        status = None
        console = None
        if use_rich:
            try:
                from rich.live import Live

                from ..utils.rich_progress import SubagentStatus

                console = get_console()
                status = SubagentStatus(
                    agent_type=agent_type,
                    description=description,
                    start_time=start,
                )
                live = Live(status, console=console, refresh_per_second=12, transient=True)
                live.__enter__()
            except Exception:
                live = None
                status = None
                console = None

        def _fallback_status_line(done: bool = False) -> None:
            elapsed = time.time() - start
            suffix = (
                f" - done ({tool_count} tools, {elapsed:.1f}s)\n"
                if done
                else f" ... {tool_count} tools, {elapsed:.1f}s"
            )
            sys.stdout.write(f"\r  [{agent_type}] {description}{suffix}")
            sys.stdout.flush()

        if live is None:
            print(f"  [{agent_type}] {description}")

        had_error = False
        try:
            while True:
                if status is not None:
                    status.phase = "thinking"
                    status.current_tool = None
                    status.tool_count = tool_count
                    status.tick()

                response = await self.client.create_message_async(
                    messages=sub_messages,
                    system=sub_system,
                    tools=sub_tools,
                    max_tokens=8000,
                )

                choice = response.choices[0]
                assistant_message = choice.message
                finish_reason = choice.finish_reason
                content = assistant_message.content or ""
                tool_calls = assistant_message.tool_calls or []

                if finish_reason != "tool_calls":
                    break

                if not tool_calls:
                    break

                results: list[dict[str, str]] = []

                for tc in tool_calls:
                    tool_count += 1
                    tc_name = tc.function.name
                    tc_input, parse_note = parse_tool_arguments(tc.function.arguments)
                    if status is not None:
                        status.phase = "tool"
                        status.current_tool = tc_name
                        status.tool_count = tool_count
                        status.tick()
                    if tc_input is None:
                        output = (
                            f"Error: Invalid JSON arguments for tool '{tc_name}': {parse_note}. "
                            f"raw={tc.function.arguments!r}"
                        )[:50000]
                        results.append({"tool_call_id": tc.id, "output": output})
                        if status is None:
                            _fallback_status_line()
                        continue

                    blocked, reason = await self.hook_manager.trigger_pre_tool_call(
                        tc_name, tc_input
                    )
                    if blocked:
                        output = f"Blocked by hook: {reason}"
                    else:
                        output = await self.tool_registry.execute(tc_name, tc_input)
                        await self.hook_manager.trigger_post_tool_call(
                            tc_name, tc_input, output
                        )
                    if parse_note:
                        output = f"Warning: {parse_note}\n{output}"
                    results.append({"tool_call_id": tc.id, "output": output})

                    if status is None:
                        _fallback_status_line()

                assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
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
                sub_messages.append(assistant_msg)
                for r in results:
                    sub_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": r["tool_call_id"],
                            "content": r["output"],
                        }
                    )
        except Exception as e:
            had_error = True
            if status is not None:
                status.phase = "error"
                status.current_tool = None
                status.extra = str(e)
                status.tool_count = tool_count
                status.tick()
            raise
        finally:
            if status is not None and not had_error:
                status.phase = "done"
                status.current_tool = None
                status.tool_count = tool_count
                status.tick()

            if live is not None:
                try:
                    live.__exit__(None, None, None)
                except Exception:
                    pass
                live = None

            if not had_error:
                if status is None:
                    _fallback_status_line(done=True)
                elif console is not None:
                    elapsed = time.time() - start
                    console.print(
                        f"  [{agent_type}] {description} - done ({tool_count} tools, {elapsed:.1f}s)"
                    )

        if content:
            return content
        return "(subagent returned no text)"
