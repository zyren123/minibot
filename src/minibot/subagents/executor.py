"""Subagent executor."""

import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config.schema import LLMConfig, RuntimeBudgetConfig
from ..core.artifacts import current_artifacts
from ..core.client import LLMClient
from ..core.model_attempt import model_attempt
from ..core.model_reply import decode_completion_reply, reported_completion_usage
from ..core.runtime_budget import RuntimeBudget, current_budget
from ..core.tool_args import parse_tool_arguments
from ..tools.registry import ToolRegistry
from ..tools.result import ToolOutcome
from ..tools.execution import execute_with_hooks
from ..hooks.manager import HookManager
from .registry import AgentRegistry, AgentType
from ..traces import RunTrace, SubagentTrace
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
        budget_limits: RuntimeBudgetConfig | None = None,
        llm_config: LLMConfig | None = None,
    ):
        self.client = client
        self.tool_registry = tool_registry
        self.agent_registry = agent_registry
        self.hook_manager = hook_manager
        self.workdir = workdir
        self.skill_loader = skill_loader
        self.budget_limits = budget_limits or RuntimeBudgetConfig()
        self.llm_config = llm_config or LLMConfig()

    def _get_tools_for_agent(self, agent_type: str) -> list[dict]:
        """Get tool definitions for an agent type."""
        agent = self.agent_registry.get(agent_type)
        if agent is None:
            return []

        excluded = {"Task", "TeamCreate", "TeamMembers", "TeamTask", "TeamMessage", "TeamBroadcast", "TeamWait", "TeamShutdown"}
        if not agent.skills_enabled:
            excluded.add("Skill")

        allowed_names = None if agent.tools == "*" else set(agent.tools)
        if allowed_names is not None and agent.skills_enabled:
            allowed_names.add("Skill")

        return [
            tool.to_dict()
            for tool in self.tool_registry.get_all()
            if tool.name not in excluded and (allowed_names is None or tool.name in allowed_names)
        ]

    async def execute(
        self,
        description: str,
        prompt: str,
        agent_type: str,
        *,
        trace: RunTrace | None = None,
    ) -> str:
        """Execute a subagent task."""
        agent = self.agent_registry.get(agent_type)
        if agent is None:
            return f"Error: Unknown agent type '{agent_type}'"
        if not agent.enabled:
            return f"Error: Subagent '{agent_type}' is disabled"
        if agent.handler is not None:
            return await agent.handler(description, prompt, agent)

        with trace.subagent(agent_type) if trace is not None else nullcontext() as child:
            scope = current_artifacts.get()
            child_scope = replace(
                scope, run_id=child.run_id if child is not None else uuid4().hex,
                observe=child.observe_artifact if child is not None else None,
            ) if scope is not None else None
            token = current_artifacts.set(child_scope)
            try:
                return await self._execute_llm(description, prompt, agent_type, agent, child)
            finally:
                current_artifacts.reset(token)

    async def _execute_llm(
        self,
        description: str,
        prompt: str,
        agent_type: str,
        agent: AgentType,
        child: SubagentTrace | None,
    ) -> str:
        skills_section = ""
        if agent.skills_enabled and self.skill_loader:
            skills_desc = self.skill_loader.get_descriptions()
            skills_section = f"\n\nAvailable Skills:\n{skills_desc}\nUse the Skill tool to load specialized knowledge when needed."

        sub_system = f"""You are a {agent_type} subagent at {self.workdir}.

{agent.prompt}{skills_section}

Complete the task and return a clear, concise summary."""

        budget = current_budget.get() or RuntimeBudget(self.budget_limits)
        sub_tools = self._get_tools_for_agent(agent_type)
        allowed_tool_names = frozenset(tool["function"]["name"] for tool in sub_tools)
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

                async with model_attempt(budget, self.llm_config, sub_messages, sub_system, sub_tools) as attempt:
                    if child is not None:
                        child.start_model()
                    response = await attempt.dispatch(self.client.create_message_async)
                    usage = reported_completion_usage(response)
                    if child is not None:
                        child.finish_model(usage)
                    reply = decode_completion_reply(response, usage)
                    attempt.settle(reply.charge)

                content = reply.content
                tool_calls = reply.tool_calls
                if reply.finish_reason != "tool_calls":
                    break

                if not tool_calls:
                    break

                results: list[dict[str, Any]] = []
                assistant_calls: list[dict[str, Any]] = []

                for index in range(len(tool_calls)):
                    budget.start_tool()
                    tool_count += 1
                    tc = tool_calls[index]
                    tc_name = tc.name
                    if child is not None:
                        child.start_tool(tc.id, tc_name)
                    if status is not None:
                        status.phase = "tool"
                        status.current_tool = tc_name
                        status.tool_count = tool_count
                        status.tick()

                    if tc_name not in allowed_tool_names:
                        outcome = ToolOutcome(f"Tool '{tc_name}' is not available to this subagent.", "refused")
                    else:
                        tc_input, parse_note = parse_tool_arguments(tc.arguments)
                        if tc_input is None:
                            output = (
                                f"Error: Invalid JSON arguments for tool '{tc_name}': {parse_note}. "
                                f"raw={tc.arguments!r}"
                            )[:50000]
                            outcome = ToolOutcome(output, "invalid_arguments")
                        else:
                            async with budget.time_limit():
                                outcome = await execute_with_hooks(
                                    self.tool_registry, self.hook_manager, tc_name, tc_input, actor=f"subagent:{agent_type}",
                                )
                            if parse_note and outcome.artifact_id is None and outcome.artifact_eligible:
                                outcome = replace(outcome, text=f"Warning: {parse_note}\n{outcome.text}")
                    if child is not None:
                        child.finish_tool(outcome)
                    results.append({"tool_call_id": tc.id, "output": outcome.text, "metadata": outcome.metadata()})
                    assistant_calls.append(tc.as_message())

                    if status is None:
                        _fallback_status_line()

                assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
                assistant_msg["tool_calls"] = assistant_calls
                sub_messages.append(assistant_msg)
                for r in results:
                    sub_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": r["tool_call_id"],
                            "content": r["output"],
                            **r["metadata"],
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
