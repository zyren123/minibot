"""Verify the restricted workspace boundary without requiring a daemon."""

import asyncio
import builtins
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.minibot import Minibot
from src.minibot.config import BashConfig, Config, LLMConfig, ToolsConfig, WorkspaceConfig, load_config
from src.minibot.config.schema import HookDefinition, HooksConfig, MCPConfig, MCPServerConfig
from src.minibot.core.run_store import DurableRunError
from src.minibot.core.workspace import WorkspaceIdentity, legacy_workspace_identity
from src.minibot.server.manager import AgentManager
from src.minibot.teams.context import TeamExecutionContext, team_execution_context
from src.minibot.tools.builtin import bash_execution as execution
from src.minibot.tools.builtin.file import EditFileTool, ReadFileTool, WriteFileTool
from src.minibot.tools.function import FunctionTool


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return Config(
        workdir=root, app_home=tmp_path / "runtime", llm=LLMConfig(api_key="test"),
        workspace=WorkspaceConfig(backend="docker"),
    )


def test_load_restricted_defaults_from_trusted_utf8_config(config, monkeypatch):
    trusted = config.app_home / "config" / "default.yaml"
    trusted.parent.mkdir(parents=True)
    trusted.write_text(
        "workspace:\n  backend: docker\n  network: bridge\n  timeout: 1.5\nllm:\n  model: 安全默认值\n",
        encoding="utf-8",
    )
    (config.workdir / ".env.example").write_text("OPENAI_BASE_URL=https://untrusted.invalid\n", encoding="utf-8")
    project_config = config.workdir / "config"
    project_config.mkdir()
    (project_config / "hooks.yaml").write_text(
        "hooks:\n  - event: session_start\n    handler: untrusted:run\n", encoding="utf-8",
    )
    (project_config / "mcp_servers.yaml").write_text(
        "servers:\n  - name: untrusted\n    command: untrusted\n", encoding="utf-8",
    )

    observed = []
    open_file = builtins.open

    def tracked(path, *args, **kwargs):
        if Path(path) == trusted:
            observed.append(kwargs.get("encoding"))
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracked)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    loaded = load_config(workdir=config.workdir, app_home=config.app_home, project_root=config.workdir)
    assert loaded.workspace == WorkspaceConfig(backend="docker", network="bridge", timeout=1.5)
    assert loaded.llm.model == "安全默认值"
    assert loaded.llm.base_url is None
    assert loaded.mcp.enabled and loaded.hooks.enabled
    assert loaded.mcp.servers == [] and loaded.hooks.hooks == []
    assert not (config.app_home / ".env").exists()
    assert observed == ["utf-8"]


def test_workspace_env_cannot_downgrade_the_backend(config, monkeypatch):
    monkeypatch.delenv("MINIBOT_WORKSPACE_BACKEND", raising=False)
    default = config.app_home / "config" / "default.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("workspace:\n  backend: ${MINIBOT_WORKSPACE_BACKEND:docker}\n", encoding="utf-8")
    untrusted = config.workdir / "untrusted.env"
    untrusted.write_text("MINIBOT_WORKSPACE_BACKEND=local\n", encoding="utf-8")
    (config.app_home / ".env").symlink_to(untrusted)

    with pytest.raises(ValueError, match="Workspace backend configuration must be outside"):
        load_config(workdir=config.workdir, app_home=config.app_home)


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink", [False, True])
async def test_replaced_workspace_cannot_be_mounted_or_resumed(config, symlink):
    config.durable_execution = True
    bot = Minibot(config=config, session_id="replaced-root", sessions_dir=config.app_home / "sessions")
    run_id = uuid4().hex
    with bot.agent.run_store.claim(bot.session_id, run_id, new=True) as journal:
        journal.create([{"role": "user", "content": "Finish this run"}], None, None)

    root = config.workdir
    root.rename(root.with_name("original-workspace"))
    if symlink:
        replacement = root.with_name("different-workspace")
        replacement.mkdir()
        root.symlink_to(replacement, target_is_directory=True)
    else:
        root.mkdir()

    with pytest.raises(ValueError, match="restricted Docker workspace"):
        await bot.resume_run(run_id)
    call = await bot.agent.tool_registry.execute_result("bash", {"command": "touch wrong-workspace"})
    assert call.is_error and "restricted Docker workspace" in call.text
    assert not (root / "wrong-workspace").exists()

    reopened = Minibot(config=config, session_id=bot.session_id)
    assert reopened.agent.workspace_identity != bot.agent.workspace_identity
    with pytest.raises(DurableRunError, match="unavailable in this workspace and session"):
        await reopened.resume_run(run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "edit"])
async def test_trusted_team_file_tool_uses_the_resolved_locked_target(tmp_path, operation):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("before", encoding="utf-8")
    second.write_text("untouched", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(first)

    class TeamRuntime:
        async def acquire_file_lock(self, *, member_id, path):
            assert (member_id, Path(path)) == ("worker", first.resolve())
            alias.unlink()
            alias.symlink_to(second)
            return True, None

    context = TeamExecutionContext(role="teammate", team_id="team", member_id="worker", runtime=TeamRuntime())
    with team_execution_context(context):
        if operation == "write":
            result = await WriteFileTool(tmp_path).execute_result("alias.txt", "after")
        else:
            result = await EditFileTool(tmp_path).execute_result("alias.txt", "before", "after")

    assert result.status == "success"
    assert first.read_text(encoding="utf-8") == "after"
    assert second.read_text(encoding="utf-8") == "untouched"


@pytest.mark.asyncio
async def test_restricted_admin_does_not_scan_or_mutate_host_skills(config, monkeypatch):
    monkeypatch.setenv("MINIBOT_HOME", str(config.app_home))
    manager = AgentManager(workdir=config.workdir)
    manager._base_config.workspace = config.workspace

    def scan():
        pytest.fail("The restricted workspace must not read host skills")

    monkeypatch.setattr(manager, "_skill_records", scan)
    assert manager.skills_snapshot() == []
    with pytest.raises(ValueError, match="Host skills are unavailable"):
        await manager.create_skill({"name": "blocked", "scope": "project"})
    with pytest.raises(ValueError, match="Host skills are unavailable"):
        await manager.delete_skill(scope="user", folder_name="blocked")


@pytest.mark.parametrize(
    "options, message",
    [
        ({"file_checkpoints": True}, "Git file checkpoints"),
        ({"hooks": HooksConfig(hooks=[HookDefinition("session_start", "untrusted:run")])}, "Host hooks"),
        ({"mcp": MCPConfig(servers=[MCPServerConfig("test", command="host-only")])}, "Host MCP"),
    ],
)
def test_unsupported_configuration_refused_before_session_setup(config, options, message):
    with pytest.raises(ValueError, match=message):
        Minibot(config=replace(config, **options))
    assert not config.app_home.exists()


@pytest.mark.parametrize("option", ["app_home", "sessions", "trace", "aliased_artifacts"])
def test_runtime_storage_cannot_resolve_inside_mount(config, option):
    inside = config.workdir / "inside"
    if option == "app_home":
        config = replace(config, app_home=inside)
    elif option == "sessions":
        with pytest.raises(ValueError, match="session.sessions_dir"):
            Minibot(config=config, sessions_dir=inside)
        assert not inside.exists()
        return
    elif option == "trace":
        config = replace(config, trace_dir=inside)
    else:
        config.app_home.mkdir()
        (config.app_home / "artifacts").symlink_to(inside, target_is_directory=True)
    with pytest.raises(ValueError, match="must be outside"):
        Minibot(config=config)
    assert not inside.exists()


@pytest.mark.asyncio
async def test_no_host_registry_skills_memory_or_delegation(config, tmp_path):
    def host_tool() -> str:
        pytest.fail("host callback executed")

    with pytest.raises(ValueError, match="Custom Python tools"):
        Minibot(config=config, tools=[host_tool])
    assert not config.app_home.exists()

    skill = tmp_path / "skills" / "outside"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: outside\ndescription: outside contents\n---\nSENSITIVE HOST CONTENT", encoding="utf-8",
    )

    bot = Minibot(config=config, skills_dir=skill.parent)
    try:
        bot.agent.set_disabled_skills([])
        assert bot.agent.skill_loader.skills_dirs == []
        assert "outside contents" not in bot.agent.system_prompt
        assert bot.agent.memory_manager is None and bot.agent.team_runtime is None
        assert all(name in bot.agent.tool_registry for name in (
            "bash", "read_file", "write_file", "edit_file", "list_dir", "read_artifact", "search_artifact",
        ))
        assert all(name not in bot.agent.tool_registry for name in ("Skill", "Task", "TeamCreate", "read_memory"))

        with pytest.raises(ValueError, match="Custom Python tools"):
            bot.register_tool(host_tool)
        with pytest.raises(ValueError, match="Host tools"):
            bot.agent.tool_registry.register(FunctionTool(host_tool, name="read_file"))
        with pytest.raises(ValueError, match="Team execution"):
            await bot.agent.set_team_tools_enabled(enabled=True)
        assert await bot.connect_mcp() == {}
        outcome = await bot.agent.tool_registry.execute_result("host_tool", {})
        assert outcome.status == "unknown_tool"
    finally:
        await bot.agent.close_client()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["tool_plugins", "attached_subagent_bot_ids"])
async def test_web_bot_refuses_host_paths_before_import(config, monkeypatch, key):
    monkeypatch.setattr("src.minibot.server.manager.load_config", lambda **kwargs: config)
    manager = AgentManager(workdir=config.workdir)
    monkeypatch.setattr(manager._bot_store, "read_bot_json", lambda bot_id: {key: ["configured"]})

    def import_plugin(paths):
        pytest.fail("host plugin imported")

    monkeypatch.setattr(manager, "_load_plugin_tools", import_plugin)
    with pytest.raises(ValueError, match="unavailable in a restricted Docker workspace"):
        await manager.get_bot("default", "session")


@pytest.mark.asyncio
async def test_docker_startup_failure_does_not_write_a_host_file(config, monkeypatch):
    root = config.workdir
    calls = []

    async def fake_process(*args, **kwargs):
        calls.append(args)
        assert args[0] == "trusted-docker"
        assert args[1:3] == ("image", "inspect")
        return execution.ExecutionResult("", "image unavailable", 1)

    monkeypatch.setenv("DOCKER_HOST", "unix:///test.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(execution, "_trusted_docker_cli", lambda workdir, env: "trusted-docker")
    monkeypatch.setattr(execution, "_process", fake_process)
    bot = Minibot(config=config)
    try:
        for name, args in (
            ("write_file", {"path": "test.txt", "content": "write"}),
            ("read_file", {"path": "test.txt"}),
            ("edit_file", {"path": "test.txt", "old_text": "old", "new_text": "new"}),
            ("list_dir", {"path": "."}),
            ("bash", {"command": "touch test.txt"}),
        ):
            result = await bot.agent.tool_registry.execute_result(name, args)
            assert result.status == "error" and "image unavailable" in result.text
        assert len(calls) == 5
        assert list(root.iterdir()) == []
    finally:
        await bot.agent.close_client()


@pytest.mark.asyncio
async def test_file_cancellation_removes_container_and_never_replays_locally(config, monkeypatch):
    entered = asyncio.Event()
    calls = []

    async def fake_process(*args, **kwargs):
        verb = args[1]
        calls.append(verb)
        if verb == "start":
            assert args[2:4] == ("--attach", "--interactive")
            assert json.loads(kwargs["stdin"])["content"] == "\"; touch escaped"
            entered.set()
            await asyncio.Future()
        output = {
            "image": '[{"Id": "trusted-image", "Config": {}}]',
            "create": "container-id",
            "rm": "removed",
            "ps": "",
        }[verb]
        return execution.ExecutionResult(output, "", 0)

    monkeypatch.setenv("DOCKER_HOST", "unix:///test.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(execution, "_trusted_docker_cli", lambda workdir, env: "trusted-docker")
    monkeypatch.setattr(execution, "_process", fake_process)
    bot = Minibot(config=config)
    try:
        task = asyncio.create_task(bot.agent.tool_registry.execute_result(
            "write_file", {"path": "test.txt", "content": "\"; touch escaped"},
        ))
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["image", "create", "start", "rm", "ps"]
        assert list(config.workdir.iterdir()) == []
    finally:
        await bot.agent.close_client()


@pytest.mark.asyncio
async def test_legacy_modes_remain_local_for_files_and_keep_identity(config):
    for policy in ("disabled", "local", "docker"):
        cfg = replace(config, workspace=WorkspaceConfig(), tools=ToolsConfig(bash=BashConfig(backend=policy)))
        bot = Minibot(config=cfg, session_id="legacy")
        try:
            tools = bot.agent.tool_registry
            assert bot.agent.workspace_identity == legacy_workspace_identity(cfg)
            assert ("bash" in tools) == (policy != "disabled")
            await tools.execute("write_file", {"path": "local.txt", "content": "café\n\n"})
            assert await tools.execute("read_file", {"path": "local.txt"}) == "café\n"
            assert "local.txt" in json.loads(await tools.execute("list_dir", {"path": "."}))
        finally:
            await bot.agent.close_client()


@pytest.mark.asyncio
async def test_workspace_identity_override_and_retry_use_owned_backend(config):
    from src.minibot.agent import Agent

    with pytest.raises(ValueError, match="identity differs"):
        Agent(config, workspace_identity=WorkspaceIdentity("fake"))
    local = Minibot(config=replace(config, workspace=WorkspaceConfig()))
    try:
        assert local.agent._durable_retry_allowed("read_file")
        assert local.agent._durable_retry_allowed("list_dir")
        local.register_tool(ReadFileTool(config.workdir))
        assert not local.agent._durable_retry_allowed("read_file")
    finally:
        await local.agent.close_client()
