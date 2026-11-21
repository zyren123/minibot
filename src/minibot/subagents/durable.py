"""Run a journaled Task in a private Docker workspace."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID, uuid5

from ..config.schema import Config, HooksConfig, MCPConfig, SessionConfig, WorkspaceConfig
from ..core.run_store import ChildRun, DurableRunBusy, DurableRunError, DurableRunPaused, RunJournal, StepState
from ..core.runtime_budget import RuntimeBudgetExceeded, current_budget
from ..core.workspace import DockerContainer, configured_workspace_identity, restricted_root_key
from ..session.store import SessionStore
from ..tools.builtin.bash_execution import DockerCommand, DockerEngine
from ..tools.builtin.workspace_backend import DockerBackend
from ..tools.result import ToolOutcome

if TYPE_CHECKING:
    from ..agent import Agent
    from .registry import AgentType

_SANDBOX_TOOLS = (
    "bash", "read_file", "list_dir", "write_file", "edit_file", "TodoWrite", "read_artifact", "search_artifact",
)
_T = TypeVar("_T")


@dataclass(frozen=True)
class ChildExecution:
    workspace: WorkspaceConfig
    engine: DockerEngine
    agent_type: str
    system_prompt: str
    tools: tuple[str, ...]
    bash_disabled: bool

    @classmethod
    def load(cls, data: dict[str, Any]) -> ChildExecution:
        if data.get("version") != 1:
            raise DurableRunError("Unsupported child execution profile.")
        workspace = WorkspaceConfig(**data["workspace"])
        if not workspace.restricted or workspace.network != "none":
            raise DurableRunError("Child execution requires a network-isolated Docker workspace.")
        return cls(
            workspace, DockerEngine(**data["engine"]), data["agent_type"], data["system_prompt"],
            tuple(data["tools"]), data["bash_disabled"],
        )


@dataclass(frozen=True)
class _DockerLease:
    journal: RunJournal
    engine: DockerEngine

    def pending(self) -> DockerContainer | None:
        return self.journal.pending_container()

    def reserve(self, name: str) -> None:
        self.journal.reserve_container(name)

    def confirm(self, name: str, container_id: str) -> None:
        self.journal.confirm_container(name, container_id)

    def release(self, name: str) -> None:
        self.journal.release_container(name)


async def _watch(operation: Awaitable[_T], check_pause: Callable[[], None]) -> _T:
    task = asyncio.ensure_future(operation)
    try:
        while not task.done():
            check_pause()
            await asyncio.wait({task}, timeout=0.2)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            settled = asyncio.gather(task, return_exceptions=True)
            while not settled.done():
                try:
                    await asyncio.shield(settled)
                except asyncio.CancelledError:
                    continue


class ChildAgentFactory:
    """Use the parent's model connection with a sealed sandbox tool registry."""

    def __init__(self, parent: Agent):
        self.parent = parent

    def config(self, root: Path, sessions_dir: str, execution: ChildExecution) -> Config:
        parent = self.parent.config
        return replace(
            parent, workdir=root, project_root=root, workspace=execution.workspace,
            tools=replace(parent.tools, bash=replace(
                parent.tools.bash, backend="disabled" if execution.bash_disabled else "local",
            )),
            file_checkpoints=False, hooks=HooksConfig(enabled=False), mcp=MCPConfig(enabled=False),
            session=SessionConfig(enabled=parent.session.enabled, sessions_dir=sessions_dir),
        )

    def build(self, child: ChildRun, config: Config, execution: ChildExecution, backend: DockerBackend) -> Agent:
        from ..agent import Agent

        role = self.parent.agent_registry.get(execution.agent_type)
        if role is None or not role.enabled or role.handler is not None:
            raise DurableRunError("The child's agent type is no longer enabled for sandbox execution.")
        tools = tuple(name for name in execution.tools if name in self.allowed_tools(role))
        agent = Agent(
            config=config, session_id=child.run.session_id, sandbox_backend=backend,
            model_client=self.parent.client, tool_allowlist=tools, extra_system_prompt=execution.system_prompt,
        )
        agent._permission_actor = f"child:{child.run.run_id}"
        agent.silent = True
        # Children use separate traces while sharing the trusted model connection.
        agent.set_stream_enabled(False)
        return agent

    def allowed_tools(self, role: AgentType) -> tuple[str, ...]:
        policy = self.parent.config.tools
        return tuple(
            name for name in _SANDBOX_TOOLS
            if (role.tools == "*" or name in role.tools)
            and ("*" in policy.enabled or name in policy.enabled)
            and name not in policy.disabled
            and (name != "bash" or policy.bash.backend != "disabled")
        )


class DurableSubagents:
    def __init__(self, parent: Agent) -> None:
        self.parent = parent
        self.factory = ChildAgentFactory(parent)

    @staticmethod
    def _private_directory(path: Path) -> None:
        key = restricted_root_key(path)
        info = path.lstat()
        if (info.st_dev, info.st_ino) != key or (
            os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
        ):
            raise DurableRunError("The sandbox workspace directory is not private.")

    async def _create(self, journal: RunJournal, step: StepState, args: dict[str, str], *, ordinal: int = 0) -> ChildRun:
        role = self.parent.agent_registry.get(args["agent_type"])
        if role is None or not role.enabled or role.handler is not None:
            raise ValueError("This agent type is unavailable in a durable Docker workspace.")
        child_id = uuid5(UUID(step.step_id), str(ordinal)).hex
        pool = journal.store.root / "child-workspaces"
        pool.mkdir(mode=0o700, exist_ok=True)
        self._private_directory(pool)
        root = pool / child_id
        root.mkdir(mode=0o700, exist_ok=True)
        self._private_directory(root)
        if any(root.iterdir()):
            raise DurableRunError("Unregistered child workspace is not empty.")
        parent = self.parent.config
        workspace = parent.durable_child_workspace
        workspace = replace(workspace, timeout=workspace.timeout or parent.tools.timeout)
        engine, image_id = await DockerCommand(root, workspace).probe()
        execution = ChildExecution(
            replace(workspace, image=image_id), engine, role.name, role.prompt,
            self.factory.allowed_tools(role), parent.tools.bash.backend == "disabled",
        )
        config = self.factory.config(root, journal.store.sessions_dir, execution)
        return journal.create_child(
            step, child_run_id=child_id, workspace_path=root, workspace_identity=configured_workspace_identity(config),
            execution={"version": 1, **asdict(execution)}, ordinal=ordinal,
            messages=[{"role": "user", "message_id": f"msg-{child_id}", "content": args["prompt"]}],
        )

    async def _backend(
        self, child: ChildRun, journal: RunJournal,
    ) -> tuple[Config, ChildExecution, DockerBackend]:
        try:
            self._private_directory(child.workspace_path.parent)
            self._private_directory(child.workspace_path)
            execution = ChildExecution.load(child.execution)
            config = self.factory.config(child.workspace_path, child.run.sessions_dir, execution)
            backend = DockerBackend(config, lease=_DockerLease(journal, execution.engine))
            if backend.identity.value != child.run.workspace_id:
                raise DurableRunError("The child workspace identity changed.")
            await backend.reconcile()
            return config, execution, backend
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            # Verify the workspace and old container before recovering any tool.
            journal.require_recovery(str(exc))
            raise DurableRunPaused(f"Child {child.run.run_id} cannot recover. {exc}") from exc

    async def _run(self, child: ChildRun) -> ToolOutcome:
        store = self.parent.run_store.for_child(child)
        with store.claim(child.run.session_id, child.run.run_id) as journal:
            state = journal.state()
            if state.status in {"failed", "abandoned"}:
                return self._outcome(child, state.status, state.error or "")
            if state.status == "completed":
                return self._completed(child, journal)
            config, execution, backend = await self._backend(child, journal)
            try:
                agent = self.factory.build(child, config, execution, backend)
                publish = None
                if config.session.enabled:
                    session = SessionStore(Path(child.run.sessions_dir))

                    def publish(messages: list[dict]) -> None:
                        session.project_durable_messages(child.run.session_id, messages)

                await _watch(
                    agent._run_claimed(
                        journal, [], resume=True, parent_run_id=child.run.parent_run_id,
                        publish_messages=publish, shared_budget=current_budget.get(),
                    ),
                    journal.check_pause,
                )
            except RuntimeBudgetExceeded:
                raise
            except DurableRunError as exc:
                if journal.state().status == "failed":
                    return self._outcome(child, "failed", type(exc).__name__)
                raise DurableRunPaused(f"Child {child.run.run_id} stopped. {exc}") from exc
            except Exception as exc:
                if journal.state().status != "failed":
                    raise
                return self._outcome(child, "failed", type(exc).__name__)
            return self._completed(child, journal)

    def _completed(self, child: ChildRun, journal: RunJournal) -> ToolOutcome:
        summary = next(
            (msg.get("content", "") for msg in reversed(journal.checkpoint().messages) if msg.get("role") == "assistant"),
            "",
        )
        return self._outcome(child, "completed", summary)

    @staticmethod
    def _outcome(child: ChildRun, status: str, summary: str) -> ToolOutcome:
        return ToolOutcome(json.dumps({
            "child_run_id": child.run.run_id, "parent_step_id": child.parent_step_id,
            "session_id": child.run.session_id, "workspace_id": child.run.workspace_id,
            "status": status, "summary": summary[:8000],
        }, ensure_ascii=True), "success" if status == "completed" else "error", artifact_eligible=False)

    async def dispatch(self, journal: RunJournal, step: StepState, args: dict[str, Any]) -> ToolOutcome:
        if args.keys() != {"description", "prompt", "agent_type"} or any(
            not isinstance(value, str) or not value.strip() for value in args.values()
        ):
            return ToolOutcome("Task requires description, prompt, and agent_type strings.", "invalid_arguments")
        child = journal.child_for_step(step)
        if child is None:
            try:
                child = await self._create(journal, step, args)
            except DurableRunPaused:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                return ToolOutcome(f"Child sandbox unavailable. {exc}", "error", artifact_eligible=False)
        journal.wait_for_child(step)
        trace = self.parent._trace_context.get()
        if trace is not None:
            trace.link_durable_child(child.run.run_id, step.step_id, child.execution["agent_type"])
        try:
            return await _watch(self._run(child), journal.check_pause)
        except DurableRunBusy as exc:
            raise DurableRunPaused(f"Child {child.run.run_id} already has an execution owner.") from exc

    async def resolve(self, child: ChildRun, step_id: str, output: str, *, is_error: bool) -> None:
        store = self.parent.run_store.for_child(child)
        with store.claim(child.run.session_id, child.run.run_id) as journal:
            await self._backend(child, journal)
            journal.resolve(step_id, output, is_error=is_error)

    async def abandon(self, child: ChildRun) -> None:
        store = self.parent.run_store.for_child(child)
        with store.claim(child.run.session_id, child.run.run_id) as journal:
            await self._backend(child, journal)
            journal.abandon()
