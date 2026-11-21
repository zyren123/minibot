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
from ..core.artifacts import ArtifactError, ArtifactStore, MAX_READ_CHARS
from ..core.run_store import ChildRun, DurableRunBusy, DurableRunError, DurableRunPaused, RunJournal, StepState
from ..core.runtime_budget import RuntimeBudgetExceeded, current_budget
from ..core.workspace import DockerContainer, configured_workspace_identity, restricted_root_key
from ..session.store import SessionStore
from ..tools.builtin.bash_execution import DockerCommand, DockerEngine
from ..tools.builtin.workspace_backend import DockerBackend
from ..tools.meta.task import MAX_BATCH_CHILDREN, ReadChildArtifactTool, TaskBatchTool
from ..tools.result import ToolOutcome
from .deliverables import ChildArtifact, export_child_artifacts, parse_deliverable_paths, read_child_artifact

if TYPE_CHECKING:
    from ..agent import Agent
    from .registry import AgentType

_SANDBOX_TOOLS = (
    "bash", "read_file", "list_dir", "write_file", "edit_file", "TodoWrite", "read_artifact", "search_artifact",
)
_T = TypeVar("_T")
_TASK_FIELDS = frozenset(("description", "prompt", "agent_type"))


@dataclass(frozen=True)
class _ChildRequest:
    prompt: str
    agent_type: str
    deliverables: tuple[str, ...]

    @classmethod
    def parse(cls, args: object) -> _ChildRequest:
        if not isinstance(args, dict) or args.keys() - {"deliverables"} != _TASK_FIELDS or any(
            not isinstance(args[key], str) or not args[key].strip() for key in _TASK_FIELDS
        ):
            raise ValueError("Task requires description, prompt, and agent_type strings.")
        return cls(args["prompt"], args["agent_type"], parse_deliverable_paths(args.get("deliverables", [])))


@dataclass(frozen=True)
class ChildExecution:
    workspace: WorkspaceConfig
    engine: DockerEngine
    agent_type: str
    system_prompt: str
    tools: tuple[str, ...]
    bash_disabled: bool
    deliverables: tuple[str, ...] = ()

    @classmethod
    def load(cls, data: dict[str, Any]) -> ChildExecution:
        if data.get("version") != 1:
            raise DurableRunError("Unsupported child execution profile.")
        workspace = WorkspaceConfig(**data["workspace"])
        if not workspace.restricted or workspace.network != "none":
            raise DurableRunError("Child execution requires a network-isolated Docker workspace.")
        return cls(
            workspace, DockerEngine(**data["engine"]), data["agent_type"], data["system_prompt"],
            tuple(data["tools"]), data["bash_disabled"], parse_deliverable_paths(data.get("deliverables", [])),
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


async def _cancel_and_settle(tasks: list[asyncio.Future]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    settled = asyncio.gather(*tasks, return_exceptions=True)
    while not settled.done():
        try:
            await asyncio.shield(settled)
        except asyncio.CancelledError:
            continue


async def _watch(operation: Awaitable[_T], check_pause: Callable[[], None]) -> _T:
    task = asyncio.ensure_future(operation)
    try:
        while not task.done():
            check_pause()
            await asyncio.wait({task}, timeout=0.2)
        return task.result()
    finally:
        if not task.done():
            await _cancel_and_settle([task])


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
        prompt = execution.system_prompt
        if execution.deliverables:
            prompt += "\n\nWrite the requested UTF-8 deliverables at these workspace-relative paths: "
            prompt += json.dumps(execution.deliverables, ensure_ascii=True)
        agent = Agent(
            config=config, session_id=child.run.session_id, sandbox_backend=backend,
            model_client=self.parent.client, tool_allowlist=tools, extra_system_prompt=prompt,
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

    async def _create(
        self, journal: RunJournal, step: StepState, request: _ChildRequest, role: AgentType, *, ordinal: int,
    ) -> ChildRun:
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
            self.factory.allowed_tools(role), parent.tools.bash.backend == "disabled", request.deliverables,
        )
        config = self.factory.config(root, journal.store.sessions_dir, execution)
        return journal.create_child(
            step, child_run_id=child_id, workspace_path=root, workspace_identity=configured_workspace_identity(config),
            execution={"version": 1, **asdict(execution)}, ordinal=ordinal,
            messages=[{"role": "user", "message_id": f"msg-{child_id}", "content": request.prompt}],
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
                if journal.pending_container() is not None:
                    await self._backend(child, journal)
                return self._outcome(child, state.status, state.error or "")
            if state.status == "completed":
                await self._deliver(child, journal)
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
            await self._deliver(child, journal, backend=backend)
            return self._completed(child, journal)

    def _artifact_store(self, child: ChildRun) -> ArtifactStore:
        config = self.parent.config
        return ArtifactStore(
            config.app_home, child.workspace_path, Path(child.run.sessions_dir), child.run.session_id,
            config.context_offloading.max_artifact_bytes, workspace_id=child.run.workspace_id,
        )

    async def _deliver(
        self, child: ChildRun, journal: RunJournal, *, backend: DockerBackend | None = None,
    ) -> None:
        if journal.child_delivery() is not None:
            return
        paths = ChildExecution.load(child.execution).deliverables
        if journal.pending_container() is not None or (paths and backend is None):
            _, _, backend = await self._backend(child, journal)
        artifacts: tuple[ChildArtifact, ...] = ()
        delivery_error = None
        if paths:
            assert backend is not None
            try:
                artifacts = await export_child_artifacts(child, backend, self._artifact_store(child), paths)
            except (ArtifactError, OSError, ValueError, RuntimeError) as exc:
                if journal.pending_container() is not None:
                    raise DurableRunPaused(f"Child {child.run.run_id} requires Docker cleanup before delivery.") from exc
                delivery_error = str(exc)
        summary = next(
            (msg.get("content", "") for msg in reversed(journal.checkpoint().messages) if msg.get("role") == "assistant"),
            "",
        )
        status = "completed" if delivery_error is None else "delivery_failed"
        journal.finish_child_delivery(self._outcome(child, status, summary, artifacts, delivery_error).text)

    def _completed(self, child: ChildRun, journal: RunJournal) -> ToolOutcome:
        receipt = journal.child_delivery()
        if receipt is None:
            raise DurableRunError("Completed child has no delivery receipt.")
        status = "success" if json.loads(receipt)["status"] == "completed" else "error"
        return ToolOutcome(receipt, status, artifact_eligible=False)

    @staticmethod
    def _outcome(
        child: ChildRun, status: str, summary: str, artifacts: tuple[ChildArtifact, ...] = (),
        delivery_error: str | None = None,
    ) -> ToolOutcome:
        return ToolOutcome(json.dumps({
            "child_run_id": child.run.run_id, "parent_step_id": child.parent_step_id,
            "parent_run_id": child.run.parent_run_id,
            "session_id": child.run.session_id, "workspace_id": child.run.workspace_id,
            "status": status, "summary": summary[:8000],
            "artifacts": [asdict(artifact) for artifact in artifacts],
            **({"delivery_error": delivery_error} if delivery_error is not None else {}),
        }, ensure_ascii=True), "success" if status == "completed" else "error", artifact_eligible=False)

    async def dispatch(self, journal: RunJournal, step: StepState, args: dict[str, Any]) -> ToolOutcome:
        if step.name == ReadChildArtifactTool.name:
            return await self._read_artifact_tool(journal, args)
        batch = step.name == TaskBatchTool.name
        try:
            raw_tasks = args.get("tasks") if batch else [args]
            if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_BATCH_CHILDREN or (
                batch and args.keys() != {"tasks"}
            ):
                raise ValueError(f"TaskBatch requires 1 to {MAX_BATCH_CHILDREN} independent tasks.")
            requests = [_ChildRequest.parse(task) for task in raw_tasks]
        except ValueError as exc:
            return ToolOutcome(str(exc), "invalid_arguments", artifact_eligible=False)

        children = [journal.child_for_step(step, ordinal=index) for index in range(len(requests))]
        roles = {}
        for index, request in enumerate(requests):
            if children[index] is not None:
                continue
            role = self.parent.agent_registry.get(request.agent_type)
            if role is None or not role.enabled or role.handler is not None:
                detail = f"Agent type '{request.agent_type}' is unavailable in a durable Docker workspace."
                if any(children):
                    raise DurableRunPaused(detail)
                return ToolOutcome(detail, "invalid_arguments", artifact_eligible=False)
            roles[index] = role

        try:
            for index, role in roles.items():
                children[index] = await self._create(journal, step, requests[index], role, ordinal=index)
        except DurableRunPaused:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            if any(children):
                raise DurableRunPaused(f"Child sandbox unavailable. {exc}") from exc
            return ToolOutcome(f"Child sandbox unavailable. {exc}", "error", artifact_eligible=False)

        active_children = [child for child in children if child is not None]
        journal.wait_for_child(step)
        trace = self.parent._trace_context.get()
        if trace is not None:
            for child in active_children:
                trace.link_durable_child(child.run.run_id, step.step_id, child.execution["agent_type"])
        outcomes = await _watch(self._run_children(active_children), journal.check_pause)
        if not batch:
            return outcomes[0]
        return ToolOutcome(
            json.dumps({"children": [json.loads(outcome.text) for outcome in outcomes]}, ensure_ascii=True),
            "error" if any(outcome.is_error for outcome in outcomes) else "success", artifact_eligible=False,
        )

    async def _run_children(self, children: list[ChildRun]) -> list[ToolOutcome]:
        tasks = [asyncio.create_task(self._run(child)) for child in children]
        try:
            pending = set(tasks)
            while pending:
                completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for child, task in zip(children, tasks):
                    if task in completed:
                        try:
                            task.result()
                        except DurableRunBusy as exc:
                            raise DurableRunPaused(f"Child {child.run.run_id} already has an execution owner.") from exc
            return [task.result() for task in tasks]
        except BaseException:
            await _cancel_and_settle(tasks)
            raise

    def list_artifacts(self, child: ChildRun) -> list[ChildArtifact]:
        store = self.parent.run_store.for_child(child)
        journal = RunJournal(store, child.run.session_id, child.run.run_id)
        receipt = journal.child_delivery()
        return [ChildArtifact(**entry) for entry in json.loads(receipt)["artifacts"]] if receipt is not None else []

    def read_artifact(self, child: ChildRun, path: str) -> str:
        parse_deliverable_paths([path])
        for entry in self.list_artifacts(child):
            if entry.path == path:
                return read_child_artifact(child, self._artifact_store(child), entry)
        raise ArtifactError("No committed child deliverable at that path.")

    async def _read_artifact_tool(self, journal: RunJournal, args: dict[str, Any]) -> ToolOutcome:
        limit_max = min(self.parent.config.llm.max_tool_output_chars, MAX_READ_CHARS)
        offset, limit = args.get("offset", 0), args.get("limit", limit_max)
        if (
            args.keys() - {"child_run_id", "path", "offset", "limit"}
            or not isinstance(args.get("child_run_id"), str) or not isinstance(args.get("path"), str)
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= limit_max
        ):
            return ToolOutcome("Supply a child run ID, a deliverable path, and a valid character range.", "invalid_arguments")
        try:
            child = journal.store.get_child(journal.session_id, journal.run_id, args["child_run_id"])
            text = await asyncio.to_thread(self.read_artifact, child, args["path"])
        except ValueError as exc:
            return ToolOutcome(str(exc), "invalid_arguments", artifact_eligible=False)
        except (ArtifactError, DurableRunError) as exc:
            return ToolOutcome(str(exc), "error", artifact_eligible=False)
        if offset > len(text):
            return ToolOutcome("Offset is past the end of the deliverable.", "invalid_arguments")
        return ToolOutcome(text[offset:offset + limit], artifact_eligible=False)

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
