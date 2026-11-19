import asyncio
import json
import os
import subprocess
import sys

import pytest

from src.minibot.config import BashConfig
from src.minibot.tools.builtin.bash import BashTool
from src.minibot.tools.builtin import bash_execution as execution


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if os.name == "posix":
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state and not state.startswith("Z"))
    return True


async def wait_file(path):
    async with asyncio.timeout(5):
        while not path.exists() or not path.read_text(encoding="utf-8").strip():
            await asyncio.sleep(0.01)
    return int(path.read_text(encoding="utf-8").strip())


@pytest.mark.asyncio
async def test_success_nonzero_bounded_output_and_workspace(tmp_path):
    tool = BashTool(tmp_path)
    assert await tool.execute("printf hello") == "hello"
    assert await tool.execute("printf payload > result; cat result") == "payload"
    assert await tool.execute("exit 7") == "Error: Command exited with code 7\n(no output)"
    command = f"'{sys.executable}' -c 'import sys; print(\"x\" * 200000); sys.exit(9)'"
    result = await tool.execute(command)
    assert result.startswith("Error: Command exited with code 9\n")
    assert len(result) == 50000


@pytest.mark.asyncio
async def test_invalid_process_bytes_are_replaced(tmp_path):
    result = await BashTool(tmp_path).execute("printf '\\377'")
    assert result == "\ufffd"


@pytest.mark.asyncio
async def test_disabled_never_acquires_lock_or_spawns(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        pytest.fail("disabled Bash spawned")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    assert "disabled" in await BashTool(tmp_path, config=BashConfig(backend="disabled")).execute("touch bad")
    assert not (tmp_path / "bad").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancel_reap_shell_and_stop_background_child(tmp_path, cancel):
    tool = BashTool(tmp_path, timeout=0.3 if not cancel else 60)
    run = asyncio.create_task(tool.execute("echo $$ > shell; sleep 60 & echo $! > child; wait"))
    shell = await wait_file(tmp_path / "shell")
    child = await wait_file(tmp_path / "child")
    if cancel:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
    else:
        assert "timed out" in await run
    assert not process_alive(shell)
    assert not process_alive(child)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
async def test_normal_shell_exit_clears_background_children(tmp_path):
    result = await asyncio.wait_for(BashTool(tmp_path).execute("sleep 60 & echo $! > child; echo done"), 3)
    assert result == "done"
    assert not process_alive(await wait_file(tmp_path / "child"))


@pytest.mark.asyncio
async def test_cancel_during_spawn_recovers_handle_and_reaps(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    spawned, release = asyncio.Event(), asyncio.Event()
    handles = []

    async def delayed(*args, **kwargs):
        proc = await original(*args, **kwargs)
        handles.append(proc)
        spawned.set()
        await release.wait()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    task = asyncio.create_task(BashTool(tmp_path).execute("sleep 60"))
    await spawned.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert handles[0].returncode is not None
    assert not process_alive(handles[0].pid)


@pytest.mark.asyncio
async def test_cancel_during_normal_finalization_is_not_swallowed(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = execution._kill_tree

    async def delayed(proc):
        entered.set()
        await release.wait()
        await original(proc)

    monkeypatch.setattr(execution, "_kill_tree", delayed)
    task = asyncio.create_task(BashTool(tmp_path).execute("true"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


def fake_docker(monkeypatch, create=None, overrides=None):
    calls = []

    async def process(*args, **kwargs):
        assert args[0] == "docker", "Docker failure must never fall back to host shell"
        calls.append((args, kwargs))
        verb = args[1]
        if verb == "create" and create:
            return await create()
        if overrides and verb in overrides:
            return overrides[verb]
        output = {
            "context": json.dumps([{"Endpoints": {"docker": {"Host": "unix:///docker.sock"}}}]),
            "image": json.dumps([{"Id": "sha256:trusted", "Config": {}}]),
            "create": "container-id", "start": "output", "inspect": json.dumps({"Running": False, "ExitCode": 0, "Error": ""}),
            "rm": "removed", "ps": "",
        }[verb]
        return execution.ExecutionResult(output, "", 0)

    monkeypatch.setattr(execution, "_process", process)
    monkeypatch.setenv("DOCKER_CONTEXT", "chosen")
    monkeypatch.setenv("DOCKER_HOST", "tcp://unsafe:2375")
    return calls


@pytest.mark.asyncio
async def test_docker_policy_endpoint_precedence_and_image_identity(tmp_path, monkeypatch):
    calls = fake_docker(monkeypatch)
    assert await BashTool(tmp_path, config=BashConfig(backend="docker")).execute("echo output") == "output"
    create, options = next(call for call in calls if call[0][1] == "create")
    assert create[-3:] == ("sha256:trusted", "-c", "echo output")
    for flag in ("--init", "--read-only", "--cap-drop", "--security-opt", "--memory", "--cpus", "--pids-limit", "--tmpfs"):
        assert flag in create
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--pull") + 1] == "never"
    assert "--env" not in create
    assert options["env"]["DOCKER_HOST"] == "unix:///docker.sock"
    assert "DOCKER_CONTEXT" not in options["env"]
    assert [call[0][1] for call in calls][-2:] == ["rm", "ps"]


@pytest.mark.asyncio
async def test_cancel_create_settles_before_remove_even_with_repeated_cancel(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    settled = False

    async def create():
        nonlocal settled
        entered.set()
        await release.wait()
        settled = True
        return execution.ExecutionResult("id", "", 0)

    calls = fake_docker(monkeypatch, create=create)
    task = asyncio.create_task(BashTool(tmp_path, config=BashConfig(backend="docker")).execute("sleep 60"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not any(call[0][1] == "rm" for call in calls)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert settled
    assert [call[0][1] for call in calls][-2:] == ["rm", "ps"]


@pytest.mark.asyncio
async def test_admin_create_timeout_reports_cleanup_uncertainty(tmp_path, monkeypatch):
    async def create():
        raise TimeoutError("unresponsive daemon")
    calls = fake_docker(monkeypatch, create=create)
    result = await BashTool(tmp_path, config=BashConfig(backend="docker")).execute("touch host-file")
    assert "cleanup uncertain for minibot-bash-" in result
    assert "daemon completion cannot be proved" in result
    assert [call[0][1] for call in calls][-2:] == ["rm", "ps"]


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [
    ({"image": execution.ExecutionResult("", "missing image", 1)}, "missing image"),
    ({"start": execution.ExecutionResult("", "failed start", 1)}, "attach failed"),
    ({"rm": execution.ExecutionResult("", "failed removal", 1)}, "cleanup uncertain"),
    ({"image": execution.ExecutionResult(json.dumps([{"Id": "id", "Config": {"Volumes": {"/data": {}}}}]), "", 0)}, "image-declared volumes"),
])
async def test_docker_infrastructure_errors_fail_closed(tmp_path, monkeypatch, override, expected):
    fake_docker(monkeypatch, overrides=override)
    result = await BashTool(tmp_path, config=BashConfig(backend="docker")).execute("touch host-file")
    assert expected in result
    assert not (tmp_path / "host-file").exists()


@pytest.mark.asyncio
async def test_remote_endpoint_refused(tmp_path, monkeypatch):
    fake_docker(monkeypatch)
    monkeypatch.delenv("DOCKER_CONTEXT")
    result = await BashTool(tmp_path, config=BashConfig(backend="docker")).execute("true")
    assert "requires a local" in result


@pytest.mark.asyncio
async def test_failed_create_without_container_preserves_startup_error(tmp_path, monkeypatch):
    async def create():
        raise RuntimeError("Docker create failed: invalid image configuration")
    fake_docker(monkeypatch, create=create, overrides={"rm": execution.ExecutionResult("", "No such container", 1)})
    result = await BashTool(tmp_path, config=BashConfig(backend="docker")).execute("true")
    assert result == "Error: Docker create failed: invalid image configuration"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX detached sessions")
async def test_escaped_session_pipes_do_not_hang_cancellation(tmp_path):
    child_script = "import os,time; os.setsid(); open('escaped', 'w', encoding='utf-8').write(str(os.getpid())); time.sleep(60)"
    command = f'''echo $$ > shell; '{sys.executable}' -c "{child_script}" & sleep 60'''
    task = asyncio.create_task(BashTool(tmp_path).execute(command))
    escaped = await wait_file(tmp_path / "escaped")
    shell = await wait_file(tmp_path / "shell")
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert not process_alive(shell)
        os.kill(escaped, 0)  # The detached session is deliberately outside group ownership.
    finally:
        os.kill(escaped, 9)
