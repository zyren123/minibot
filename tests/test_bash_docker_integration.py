import asyncio
import json
import os
from types import SimpleNamespace
import subprocess
import uuid

import pytest

from src.minibot.config import BashConfig
from src.minibot.tools.builtin.bash import BashTool
from src.minibot.tools.builtin import bash_execution as execution

pytestmark = [
    pytest.mark.skipif(os.environ.get("MINIBOT_TEST_DOCKER") != "1", reason="set MINIBOT_TEST_DOCKER=1 for real Docker"),
    pytest.mark.asyncio,
]


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def container_name(monkeypatch):
    docker("info", "--format", "{{.ServerVersion}}")
    docker("image", "inspect", "debian:bookworm")
    suffix = uuid.uuid4().hex
    monkeypatch.setattr(execution.uuid, "uuid4", lambda: SimpleNamespace(hex=suffix))
    name = f"minibot-bash-{suffix}"
    yield name
    if docker("ps", "--all", "--filter", f"name=^{name}$", "--format", "{{.ID}}").strip():
        docker("rm", "-f", "--volumes", name)


def assert_absent(name):
    assert docker("ps", "--all", "--filter", f"name=^{name}$", "--format", "{{.ID}}").strip() == ""


async def wait_ready(path):
    async with asyncio.timeout(15):
        while not path.exists():
            await asyncio.sleep(0.02)


async def test_workspace_write_read_and_nonzero(tmp_path, container_name):
    tool = BashTool(tmp_path, config=BashConfig(backend="docker"))
    assert await tool.execute("printf success > result; cat result") == "success"
    assert (tmp_path / "result").read_text(encoding="utf-8") == "success"
    assert_absent(container_name)
    assert await tool.execute("printf broken >&2; exit 17") == "Error: Command exited with code 17\nbroken"
    assert_absent(container_name)


async def test_workspace_mount_supports_comma_and_space(tmp_path, container_name):
    workdir = tmp_path / "workspace, with space"
    workdir.mkdir()
    assert await BashTool(workdir, config=BashConfig(backend="docker")).execute("printf safe > result; cat result") == "safe"
    assert (workdir / "result").read_text(encoding="utf-8") == "safe"
    assert_absent(container_name)


async def test_host_secret_and_outside_file_not_exposed(tmp_path, container_name, monkeypatch):
    outside = tmp_path.parent / f"host-secret-{container_name}"
    outside.write_text("host-file-secret", encoding="utf-8")
    monkeypatch.setenv("MINIBOT_HOST_SECRET", "host-env-secret")
    try:
        command = f"test ! -e '{outside}' && test -z \"${{MINIBOT_HOST_SECRET+x}}\" && printf isolated"
        assert await BashTool(tmp_path, config=BashConfig(backend="docker")).execute(command) == "isolated"
        assert_absent(container_name)
    finally:
        outside.unlink()


async def test_running_container_policy_and_cancel_cleanup(tmp_path, container_name):
    tool = BashTool(tmp_path, config=BashConfig(backend="docker"))
    task = asyncio.create_task(tool.execute("touch /workspace/ready; sleep 60"))
    try:
        await wait_ready(tmp_path / "ready")
        spec = json.loads(docker("inspect", container_name))[0]
        host = spec["HostConfig"]
        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert host["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["Memory"] == 512 * 1024 * 1024
        assert host["NanoCpus"] == 1000000000
        assert host["PidsLimit"] == 128
        assert host["Init"] is True
        assert "/tmp" in host["Tmpfs"] and "size=64m" in host["Tmpfs"]["/tmp"]
        mounts = [mount for mount in spec["Mounts"] if mount["Type"] == "bind"]
        assert len(mounts) == 1
        assert mounts[0]["Source"] == str(tmp_path.resolve())
        assert mounts[0]["Destination"] == "/workspace"
        assert spec["Config"]["Entrypoint"] == ["/bin/sh"]
        if os.name == "posix":
            assert spec["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert_absent(container_name)


async def test_timeout_removes_real_container(tmp_path, container_name):
    result = await BashTool(tmp_path, timeout=1, config=BashConfig(backend="docker")).execute("sleep 60")
    assert "timed out" in result
    assert_absent(container_name)


async def test_missing_image_never_runs_host_command(tmp_path, container_name):
    result = await BashTool(tmp_path, config=BashConfig(backend="docker", image=f"{container_name}:missing")).execute("touch host-fallback")
    assert result.startswith("Error: Docker image inspect failed:")
    assert not (tmp_path / "host-fallback").exists()
    assert_absent(container_name)


async def test_failed_start_is_error_and_container_is_removed(tmp_path, container_name, monkeypatch):
    original = execution._process
    async def missing_entrypoint(*args, **kwargs):
        if args[:2] == ("docker", "create"):
            args = list(args)
            args[args.index("--entrypoint") + 1] = "/missing-shell"
        return await original(*args, **kwargs)
    monkeypatch.setattr(execution, "_process", missing_entrypoint)
    result = await BashTool(tmp_path, config=BashConfig(backend="docker")).execute("touch host-fallback")
    assert result.startswith("Error: Command exited with code 127\n")
    assert "/missing-shell" in result
    assert not (tmp_path / "host-fallback").exists()
    assert_absent(container_name)
