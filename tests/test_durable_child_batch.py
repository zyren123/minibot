"""Exercise concurrent children, verified delivery, and restart from a real process crash."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from src.minibot import Minibot
from src.minibot.config import Config, HooksConfig, LLMConfig, MCPConfig, TeamsConfig
from src.minibot.config.schema import MCPServerConfig, MemoryConfig, RuntimeBudgetConfig, SessionConfig
from src.minibot.core.artifacts import ArtifactError
from src.minibot.core.run_store import DurableRunError, RunJournal
from src.minibot.core.runtime_budget import RuntimeBudgetExceeded
from tests.test_durable_execution import append_line, call, lines, response, wait_for_file
from tests.test_durable_sandbox_child import kill_worker, mark_and_stop, spawn


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.environ.get("MINIBOT_TEST_DOCKER") != "1" or os.name != "posix", reason="real Docker required"),
]

_EXPORT = "nested/result.txt"
_CHILDREN = {"first": "成果 α\r\n", "second": "成果 β\r\n"}
_MCP_SERVER = """
import json
import sys
from pathlib import Path

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {"serverInfo": {"name": "host", "version": "1"}, "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": [{"name": "note", "description": "Read the host note", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        text = Path("host-note.txt").read_text(encoding="utf-8")
        result = {"content": [{"type": "text", "text": text}]}
    else:
        raise AssertionError(method)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


def bot_for(root, *, budget=None, crash_test=False):
    server = MCPServerConfig("host", command=sys.executable, args=[str(root / "mcp_peer.py")])
    cfg = Config(
        workdir=root, app_home=root / "home", durable_execution=True,
        llm=LLMConfig(api_key="offline-model", stream_enabled=False),
        session=SessionConfig(sessions_dir=str(root / "sessions")),
        teams=TeamsConfig(enabled=False), hooks=HooksConfig(enabled=False),
        memory=MemoryConfig(active_namespace="durable-parent"),
        mcp=MCPConfig(servers=[server]), runtime_budget=budget or RuntimeBudgetConfig(),
    )
    bot = Minibot(config=cfg, session_id="batch")
    entering = set()
    both_entered = asyncio.Event()

    async def model(*, messages, system, tools, **kwargs):
        tool_names = {entry["function"]["name"] for entry in tools}
        results = [msg for msg in messages if msg["role"] == "tool"]
        if "Working Directory: /workspace" in system:
            assert {"Task", "TaskBatch", "ReadChildArtifact", "read_memory", "mcp__host__note"}.isdisjoint(tool_names)
            name = messages[0]["content"]
            append_line(root / "child-requests.log", name)
            if results or budget is not None:
                if results:
                    assert results[-1]["content"] == "tested"
                return response()
            if crash_test:
                entering.add(name)
                if entering == set(_CHILDREN):
                    both_entered.set()
                async with asyncio.timeout(5):
                    await both_entered.wait()
            content = _CHILDREN[name]
            return response(
                call("write_file", {"path": _EXPORT, "content": content}, call_id="write"),
                call("write_file", {
                    "path": "check.py",
                    "content": (
                        "from pathlib import Path\n"
                        f"assert Path({_EXPORT!r}).read_bytes() == {content.encode('utf-8')!r}\n"
                        "print('tested')\n"
                    ),
                }, call_id="test-file"),
                call("bash", {"command": "python3 -I -B check.py"}, call_id="test"),
            )

        by_id = {item["tool_call_id"]: item["content"] for item in results}
        if "artifact" in by_id:
            assert by_id["artifact"] == _CHILDREN["first"]
            return response()
        if "batch" in by_id:
            receipt = json.loads(by_id["batch"])
            return response(call("ReadChildArtifact", {
                "child_run_id": receipt["children"][0]["child_run_id"], "path": _EXPORT,
            }, call_id="artifact"))
        tasks = [
            {"agent_type": "code", "description": "Produce test output", "prompt": name, "deliverables": [_EXPORT]}
            for name in _CHILDREN
        ]
        assert "TaskBatch" in tool_names
        calls = [call("TaskBatch", {"tasks": tasks}, call_id="batch")]
        if budget is None:
            assert {"mcp__host__note", "read_memory"} <= tool_names
            calls[:0] = [
                call("mcp__host__note", {}, call_id="mcp"),
                call("read_memory", {"uri": "system://boot"}, call_id="memory"),
            ]
        return response(*calls)

    bot.agent.client.create_message_async = model
    return bot


def setup_host(root):
    (root / "mcp_peer.py").write_text(_MCP_SERVER, encoding="utf-8")
    (root / "host-note.txt").write_text("trusted host MCP content", encoding="utf-8")


def worker(root):
    bot = bot_for(root, crash_test=True)
    bot.agent.memory_manager.create_namespace("durable-parent", "Durable parent")
    finish = RunJournal.finish_step

    def stop_before_parent_receipt(journal, step, result, **kwargs):
        if step.name == "TaskBatch":
            mark_and_stop(root, journal.run_id)
        return finish(journal, step, result, **kwargs)

    RunJournal.finish_step = stop_before_parent_receipt

    async def run():
        assert await bot.connect_mcp() == {"host": None}
        await bot.chat("Use two independent child workspaces and verify the first artifact")

    asyncio.run(run())


def install_worker(root, parent_id):
    bot = bot_for(root)
    for name, child in zip(_CHILDREN, bot.list_child_runs(parent_id), strict=True):
        assert bot.apply_child_artifact(parent_id, child.run.run_id, _EXPORT, f"integrated-{name}.txt")
    mark_and_stop(root, parent_id)


async def test_parallel_children_reuse_exact_deliverables_after_restart(tmp_path):
    setup_host(tmp_path)
    process = spawn(tmp_path, "batch", script=Path(__file__))
    try:
        parent_id = await wait_for_file(tmp_path / "ready", process)
    finally:
        kill_worker(process)
    bot = bot_for(tmp_path)
    children = bot.list_child_runs(parent_id)
    assert len(children) == 2
    assert len({child.run.workspace_id for child in children}) == 2
    assert [child.run.status for child in children] == ["completed", "completed"]
    assert [child.ordinal for child in children] == [0, 1]
    assert not (tmp_path / _EXPORT).exists()
    original_requests = lines(tmp_path / "child-requests.log")
    assert sorted(original_requests) == ["first", "first", "second", "second"]
    manifest_before = [bot.list_child_artifacts(parent_id, child.run.run_id) for child in children]
    for name, child, manifest in zip(_CHILDREN, children, manifest_before, strict=True):
        expected = _CHILDREN[name]
        [artifact] = manifest
        assert artifact.path == _EXPORT
        assert artifact.sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()
        assert artifact.size_bytes == len(expected.encode("utf-8"))
        assert bot.read_child_artifact(parent_id, child.run.run_id, _EXPORT) == expected

    try:
        await bot.connect_mcp()
        result = await bot.resume_run(parent_id)
        assert result.assistant_text == "done"
        steps = bot.list_run_steps(parent_id)
        assert steps[0].result["content"] == "trusted host MCP content"
        assert not steps[1].result["is_error"]
        assert "durable-parent" in steps[1].result["content"]
        batch = json.loads(steps[2].result["content"])
        assert [row["child_run_id"] for row in batch["children"]] == [child.run.run_id for child in children]
        assert [row["artifacts"][0]["artifact_id"] for row in batch["children"]] == [
            manifest[0].artifact_id for manifest in manifest_before
        ]
        assert lines(tmp_path / "child-requests.log") == original_requests
    finally:
        await bot.agent.disconnect_mcp_servers()
        await bot.agent.close_client()

    (tmp_path / "ready").unlink()
    install_process = spawn(tmp_path, "install", parent_id, script=Path(__file__))
    try:
        await wait_for_file(tmp_path / "ready", install_process)
    finally:
        kill_worker(install_process)

    reopened = bot_for(tmp_path)
    try:
        for name, child in zip(_CHILDREN, children, strict=True):
            destination = tmp_path / f"integrated-{name}.txt"
            before = destination.stat()
            assert not reopened.apply_child_artifact(parent_id, child.run.run_id, _EXPORT, destination.name)
            assert destination.read_bytes() == _CHILDREN[name].encode("utf-8")
            assert destination.stat().st_ino == before.st_ino
        with pytest.raises(ArtifactError):
            reopened.apply_child_artifact(parent_id, children[0].run.run_id, _EXPORT, "integrated-second.txt")
        with pytest.raises(DurableRunError):
            reopened.read_child_artifact(children[0].run.run_id, children[1].run.run_id, _EXPORT)
    finally:
        await reopened.agent.close_client()


async def test_parallel_children_share_parent_model_call_budget(tmp_path):
    bot = bot_for(tmp_path, budget=RuntimeBudgetConfig(max_llm_calls=2))
    try:
        with pytest.raises(RuntimeBudgetExceeded, match="max_llm_calls"):
            await bot.chat("Delegate within the parent budget")
        [parent] = bot.list_runs()
        assert parent.status == "waiting_child"
        assert len(bot.list_child_runs(parent.run_id)) == 2
        assert len(lines(tmp_path / "child-requests.log")) == 1
    finally:
        await bot.agent.close_client()


if __name__ == "__main__":
    root = Path(sys.argv[1])
    if sys.argv[2] == "install":
        install_worker(root, sys.argv[3])
    else:
        worker(root)
