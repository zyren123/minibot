"""Exercise the same mounted workspace through the SDK, shell, and file tools."""

import asyncio
from dataclasses import replace
import json
import os
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.minibot import Minibot
from src.minibot.config import Config, LLMConfig, ToolPermissionsConfig, WorkspaceConfig
from src.minibot.core.artifacts import ArtifactError, ArtifactStore
from src.minibot.core.run_store import DurableRunError
from src.minibot.tools.builtin import bash_execution as execution
from tests.test_durable_execution import call, response


pytestmark = [
    pytest.mark.skipif(os.environ.get("MINIBOT_TEST_DOCKER") != "1", reason="set MINIBOT_TEST_DOCKER=1 for real Docker"),
    pytest.mark.asyncio,
]


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return Config(
        workdir=root, app_home=tmp_path / "runtime", durable_execution=True,
        llm=LLMConfig(api_key="test-key", stream_enabled=False),
        workspace=WorkspaceConfig(backend="docker"),
    )


@pytest.fixture
def container_name(monkeypatch):
    suffix = uuid4().hex
    monkeypatch.setattr(execution, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=suffix)))
    name = f"minibot-workspace-{suffix}"
    yield name
    if exists(name):
        docker("rm", "-f", "--volumes", name)


def exists(name):
    return bool(docker("ps", "--all", "--filter", f"name=^{name}$", "--format", "{{.ID}}").strip())


async def test_agent_tools_share_files_and_scope_artifacts_and_journal(workspace, container_name):
    approvals = []

    async def allow(request):
        approvals.append((request.tool_name, json.loads(request.preview)))
        return True

    cfg = replace(workspace, tool_permissions=ToolPermissionsConfig(mode="manual", default="ask"))
    text = "初始化\n$(touch not-a-command); 'quoted' \"double-quoted\"\n"
    turns = iter([
        response(
            call("bash", {"command": "printf 'from-shell\\n' > first.txt"}, call_id="a"),
            call("read_file", {"path": "./first.txt"}, call_id="b"),
            call("write_file", {"path": "nested/数据.txt", "content": text}, call_id="c"),
            call("edit_file", {"path": "nested/数据.txt", "old_text": "初始化", "new_text": "已编辑"}, call_id="d"),
            call("bash", {"command": "cat nested/数据.txt; python3 -c 'print(\"日志 \" * 20000 + \"ERROR_END\", end=\"\")'"}, call_id="e"),
            call("list_dir", {"path": "nested"}, call_id="f"),
        ),
        response(),
    ])

    async def model(**kwargs):
        return next(turns)

    bot = Minibot(config=cfg, session_id="docker-integration", tool_approval_handler=allow)
    bot.agent.client.create_message_async = model
    result = await bot.chat("Write and edit files, then read them from the shell")
    assert result.assistant_text == "done"
    [run] = bot.list_runs()
    assert run.status == "completed"
    assert run.workspace_id == bot.agent.workspace_backend.identity.value
    [_, read, write, edit, bash, listing] = bot.list_run_steps(run.run_id)
    assert read.result["content"] == "from-shell"
    assert write.result["content"].startswith("Wrote ")
    assert edit.result["content"] == "Edited nested/数据.txt"
    assert json.loads(listing.result["content"]) == ["数据.txt"]
    assert [name for name, _ in approvals] == ["bash", "read_file", "write_file", "edit_file", "bash", "list_dir"]
    assert approvals[1][1]["path"] == "first.txt"
    assert bash.artifact_id and bash.artifact_id in bash.result["content"]
    scoped = ArtifactStore(
        cfg.app_home, cfg.workdir, cfg.app_home / "sessions", bot.session_id, cfg.context_offloading.max_artifact_bytes,
        workspace_id=run.workspace_id,
    )
    record, head = scoped.read(bash.artifact_id, 0, 4096)
    tail_record, matches = scoped.search(bash.artifact_id, "ERROR_END")
    assert record == tail_record
    assert record.run_id == run.run_id and record.workspace_id == run.workspace_id
    assert head.startswith("已编辑\n$(touch not-a-command)")
    assert matches[0].snippet.endswith("ERROR_END")
    assert scoped.directory.is_relative_to(cfg.app_home) and not scoped.directory.is_relative_to(cfg.workdir)
    assert not (cfg.workdir / "not-a-command").exists()
    large = "🐈" * 40_000
    payload_write = await bot.agent.tool_registry.execute_result("write_file", {"path": "large.txt", "content": large})
    assert payload_write.status == "success", payload_write.text
    assert (cfg.workdir / "large.txt").read_text(encoding="utf-8") == large
    assert not exists(container_name)

    reopened = Minibot(config=cfg, session_id=bot.session_id)
    assert reopened.list_runs() == [run]
    local_cfg = replace(cfg, workspace=WorkspaceConfig())
    local = Minibot(config=local_cfg, session_id=bot.session_id)
    changed = Minibot(config=replace(cfg, workspace=replace(cfg.workspace, network="bridge")), session_id=bot.session_id)
    for other in (local, changed):
        assert other.list_runs() == []
        with pytest.raises(DurableRunError, match="unavailable"):
            await other.resume_run(run.run_id)
    local_store = ArtifactStore(
        cfg.app_home, cfg.workdir, cfg.app_home / "sessions", bot.session_id, cfg.context_offloading.max_artifact_bytes,
    )
    legacy = local_store.save("old trusted data", "old-run", "read_file")
    assert local_store.read(legacy.artifact_id, 0, 20)[1] == "old trusted data"
    for store, foreign in ((local_store, bash.artifact_id), (scoped, legacy.artifact_id)):
        with pytest.raises(ArtifactError, match="unavailable"):
            store.read(foreign, 0, 20)
    for instance in (bot, reopened, local, changed):
        await instance.agent.close_client()


async def test_restricted_paths_and_special_files_do_not_touch_host(workspace, container_name, tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("private host text", encoding="utf-8")
    bot = Minibot(config=workspace)
    try:
        tools = bot.agent.tool_registry
        made = await tools.execute_result("bash", {"command": (
            "ln -s /etc/passwd file-link; ln -s /etc nested-link; ln -s . inside-link; "
            "printf 'data' > ordinary; ln ordinary hard-link; mkfifo pipe"
        )})
        assert made.status == "success", made.text
        for tool, args in (
            ("read_file", {"path": "../private.txt"}),
            ("read_file", {"path": str(outside)}),
            ("read_file", {"path": "dir/../ordinary"}),
            ("read_file", {"path": "file-link"}),
            ("read_file", {"path": "inside-link/ordinary"}),
            ("read_file", {"path": "hard-link"}),
            ("read_file", {"path": "pipe"}),
            ("write_file", {"path": "nested-link/would-write", "content": "secret"}),
            ("write_file", {"path": "file-link", "content": "secret"}),
            ("edit_file", {"path": "file-link", "old_text": "root", "new_text": "changed"}),
            ("list_dir", {"path": "nested-link"}),
        ):
            result = await tools.execute_result(tool, args)
            assert result.status == "error", result.text
            assert "private host text" not in result.text
        assert (workspace.workdir / "file-link").is_symlink()
        assert outside.read_text(encoding="utf-8") == "private host text"
        assert not exists(container_name)
    finally:
        await bot.agent.close_client()


@pytest.mark.parametrize("cancel", [False, True])
async def test_restricted_policy_and_cleanup(workspace, container_name, monkeypatch, cancel, tmp_path):
    policy = replace(workspace.workspace, timeout=15 if cancel else 5, memory_mb=96, cpus=0.5, pids_limit=48)
    cfg = replace(workspace, workspace=policy)
    monkeypatch.setenv("TEST_WORKSPACE_PRIVATE", "host-only")
    cli_dir = tmp_path / "docker-config"
    cli_dir.mkdir()
    (cli_dir / "config.json").write_text(
        json.dumps({"proxies": {"default": {"httpProxy": "http://host-proxy.invalid"}}}), encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(cli_dir))
    bot = Minibot(config=cfg)
    try:
        tools = bot.agent.tool_registry
        run = asyncio.create_task(tools.execute_result("bash", {"command": (
            "printf '%s|%s' \"${TEST_WORKSPACE_PRIVATE-unset}\" \"${HTTP_PROXY-unset}\" > ready; sleep 60"
        )}))
        try:
            async with asyncio.timeout(10):
                ready = cfg.workdir / "ready"
                while not ready.exists() or not ready.stat().st_size:
                    if run.done():
                        pytest.fail((await run).text)
                    await asyncio.sleep(0.02)
            [state] = json.loads(docker("inspect", container_name))
            host = state["HostConfig"]
            assert host["Memory"] == 96 * 1024 * 1024
            assert host["NanoCpus"] == 500_000_000
            assert host["PidsLimit"] == 48
            assert host["NetworkMode"] == "none"
            assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"]
            assert state["Mounts"][0]["Destination"] == "/workspace"
            assert ready.read_text(encoding="utf-8") == "unset|unset"
            if cancel:
                run.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await run
            else:
                assert (await run).status == "timeout"
            assert not exists(container_name)
        finally:
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
    finally:
        await bot.agent.close_client()
