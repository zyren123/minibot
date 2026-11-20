"""Exercise complete artifact capture and bounded retrieval through the runtime."""

import json
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import ContextOffloadingConfig, RuntimeBudgetConfig, SessionConfig, ToolPermissionsConfig
from src.minibot.core.artifacts import ArtifactError, ArtifactStore, MAX_READ_CHARS
from src.minibot.core.model_context import prepare_model_request
from src.minibot.core.runtime_budget import RuntimeBudgetExceeded
from src.minibot.mcp.protocol import MCPTool, MCPToolResult
from src.minibot.mcp.tool_adapter import MCPToolAdapter
from src.minibot.sdk import Minibot
from src.minibot.tools.builtin.bash import BashTool
from src.minibot.tools.builtin.file import ReadFileTool
from src.minibot.tools.function import FunctionTool
from tests.test_agent_streaming import _response
from tests.test_bash_interrupt import config
from tests.test_model_context import RecordingProvider, input_tokens
from tests.test_run_traces import records
from tests.test_task_regression import tool_turn
from tests.test_tool_outcomes import call


def request(name, args, *, id="tool"):
    return call(name, json.dumps(args), id=id)


def store_for(cfg, session_id):
    return ArtifactStore(
        cfg.app_home, cfg.workdir, Path(cfg.session.sessions_dir),
        session_id, cfg.context_offloading.max_artifact_bytes,
    )


def tool_messages(messages):
    return [message for message in messages if message["role"] == "tool"]


def test_reopened_context_keeps_artifact_id_under_a_smaller_tool_limit(tmp_path):
    artifact = store_for(config(tmp_path), "previous-session").save("stored text", "run", "bash")
    reference = f"Artifact {artifact.artifact_id}"
    original = f"{reference} ({artifact.size_bytes} bytes)\npreview"
    messages = [{"role": "tool", "content": original, "tool_artifact_id": artifact.artifact_id}]

    request = prepare_model_request(messages, "", None, 50, window_tokens=512, tool_output_chars=32)

    assert request.messages[0]["content"] == reference
    assert messages[0]["content"] == original


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="exercise the POSIX shell with quoted Python")
async def test_real_bash_artifact_survives_compaction_and_sdk_reopen(tmp_path):
    cfg = config(tmp_path)
    cfg.llm.max_tool_output_chars = 32
    cfg.session = SessionConfig(sessions_dir=str(tmp_path / "sessions"))
    cfg.trace_dir = tmp_path / "traces"
    session_id = "artifact-session"
    raw = "  " + "x" * 110_000 + "ERROR42\r\n"
    code = 'import sys; sys.stdout.buffer.write(b"  " + b"x" * 110000 + b"ERROR42\\r\\n")'
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    bot = Minibot(config=cfg, session_id=session_id)
    bot.agent.silent = True
    bot.agent.client = RecordingProvider(
        tool_turn(request("bash", {"command": command})), _response(content="Output stored."),
    )
    events = [event async for event in bot.stream("Run the log generator.")]
    tool_event, = [event for event in events if event["type"] == "tool_result"]
    artifact_id = tool_event["tool_artifact_id"]
    visible = tool_messages(bot.agent.client.requests[1]["messages"])[0]
    assert visible["content"] == tool_event["tool_output"] == f"Artifact {artifact_id}"
    assert len(visible["content"]) <= 32
    assert visible["tool_status"] == "success"
    persisted, = tool_messages(bot.messages)
    assert persisted["tool_artifact_id"] == artifact_id

    finish = _response(content="Summary completed.")
    finish.usage = SimpleNamespace(prompt_tokens=80_001, completion_tokens=1, total_tokens=80_002)
    bot.agent.client = RecordingProvider(finish, _response(content="Summary omitted the log."))
    await bot.chat("Summarize the previous turn.")
    assert bot.messages[0]["is_compaction"] is True

    store = store_for(cfg, session_id)
    full = "".join(store.read(artifact_id, offset, MAX_READ_CHARS)[1] for offset in range(0, len(raw), MAX_READ_CHARS))
    assert full == raw

    class ReopenedProvider:
        model = "test"
        stage = 0

        async def create_message_async(self, *, messages, system, tools, max_tokens, **kwargs):
            assert artifact_id in system
            assert raw not in system
            assert all(len(message["content"]) <= 32 for message in tool_messages(messages))
            assert input_tokens({"system": system, "messages": messages, "tools": tools}) + max_tokens <= cfg.llm.max_context_tokens
            self.stage += 1
            if self.stage == 1:
                assert tool_messages(messages) == []
                return tool_turn(request("search_artifact", {"artifact_id": artifact_id, "query": "ERROR42"}))
            latest = tool_messages(messages)[-1]["content"]
            if self.stage == 2:
                row, = json.loads(latest)
                assert row[0] == raw.index("ERROR42")
                assert row[2].startswith("ERROR42")
                return tool_turn(request(
                    "read_artifact", {"artifact_id": artifact_id, "offset": row[0], "limit": 32}, id="read",
                ))
            assert latest == "ERROR42\r\n"
            return _response(content="ERROR42 recovered.")

    reopened = Minibot(config=cfg, session_id=session_id)
    assert reopened.messages[0]["is_compaction"] is True
    assert not tool_messages(reopened.messages)
    reopened.agent.silent = True
    reopened.agent.client = ReopenedProvider()
    await reopened.chat("Find the failure in the earlier log.")
    assert reopened.messages[-1]["content"] == "ERROR42 recovered."
    traces = [entry for run in records(cfg.trace_dir) for entry in run if entry["event"].startswith("artifact_")]
    assert {entry["event"] for entry in traces} == {"artifact_created", "artifact_search", "artifact_read"}
    created, = [entry for entry in traces if entry["event"] == "artifact_created"]
    assert created["size_bytes"] == len(raw)
    assert created["artifact_run_id"] == created["run_id"]
    assert {entry["artifact_id"] for entry in traces} == {artifact_id}
    assert "ERROR42" not in json.dumps(traces)


def test_store_has_opaque_ids_private_records_and_session_boundaries(tmp_path):
    app_home = tmp_path / "app"
    first = ArtifactStore(app_home, tmp_path, tmp_path / "sessions", "../unsafe-session", 1024)
    item = first.save("é🙂 Result\n", "run", "example")
    meta, text = first.read(item.artifact_id, 1, 2)
    assert text == "🙂 "
    assert meta == item
    assert item.size_bytes == len("é🙂 Result\n".encode("utf-8"))
    assert item.location == f"{item.artifact_id}.artifact"
    assert first.recent() == [item]
    if os.name == "posix":
        assert first.root.stat().st_mode & 0o777 == 0o700
        assert first.directory.stat().st_mode & 0o777 == 0o700
        assert (first.directory / item.location).stat().st_mode & 0o777 == 0o600
    for other in (
        ArtifactStore(app_home, tmp_path, tmp_path / "alternative", "../unsafe-session", 1024),
        ArtifactStore(app_home, tmp_path / "nested", tmp_path / "sessions", "../unsafe-session", 1024),
        ArtifactStore(app_home, tmp_path, tmp_path / "sessions", "other", 1024),
    ):
        with pytest.raises(ArtifactError, match="unavailable"):
            other.read(item.artifact_id, 0, 2)
    for identifier in ("../secrets", "/etc/passwd", item.artifact_id + ".artifact", "a" * 22):
        with pytest.raises(ArtifactError, match="Invalid artifact ID"):
            first.read(identifier, 0, 1)
    for offset, limit in [(-1, 1), (True, 1), (99, 1), (0, 0), (0, MAX_READ_CHARS + 1)]:
        with pytest.raises(ArtifactError):
            first.read(item.artifact_id, offset, limit)


@pytest.mark.skipif(os.name != "posix", reason="test POSIX symbolic links")
def test_store_rejects_substituted_records_and_storage(tmp_path):
    source = tmp_path / "secret"
    source.write_text("secret", encoding="utf-8")
    store = ArtifactStore(tmp_path / "app", tmp_path, tmp_path / "sessions", "session", 1024)
    item = store.save("original", "run", "tool")
    record = store.directory / item.location
    record.unlink()
    record.symlink_to(source)
    with pytest.raises(ArtifactError, match="unavailable"):
        store.read(item.artifact_id, 0, 32)
    store.directory.rename(store.root / "old")
    store.directory.symlink_to(store.root / "old", target_is_directory=True)
    with pytest.raises(ArtifactError, match="not private"):
        store.save("new text", "run", "tool")
    assert source.read_text(encoding="utf-8") == "secret"


@pytest.mark.asyncio
async def test_large_file_and_mcp_outputs_keep_fidelity_and_tool_status(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.context_offloading = ContextOffloadingConfig(threshold_chars=32)
    cfg.llm.max_tool_output_chars = 80
    agent = Agent(cfg)
    agent.silent = True
    raw = "leading\r\n" + "界" * 40_000 + "\r\n尾\r\n"
    (tmp_path / "logs.txt").write_bytes(raw.encode("utf-8"))
    observed = []

    class MCP:
        name = "mock"

        async def call_tool(self, **_kwargs):
            return MCPToolResult(raw, is_error=True)

    async def post_hook(name, _args, text):
        observed.append((name, text))
        if name.startswith("mcp__"):
            raise ValueError("hook diagnostic")

    monkeypatch.setattr(agent.hook_manager, "trigger_post_tool_call", post_hook)
    agent.tool_registry.register(MCPToolAdapter(MCPTool("logs", "", {"type": "object"}), MCP()))
    repaired_model_args = '{"path":"logs.txt"} extra'
    agent.client = RecordingProvider(
        tool_turn(
            request("read_file", {"path": "logs.txt", "limit": 1}, id="small"),
            call("read_file", repaired_model_args, id="file"),
            request("mcp__mock__logs", {}, id="mcp"),
        ),
        _response(content="Done."),
    )
    history = await agent.run_loop([{"role": "user", "content": "Open logs."}])
    small, file, mcp = tool_messages(history)
    assert small["content"] == "leading"
    assert file["content"].startswith(f'Artifact {file["tool_artifact_id"]}')
    assert len(file["content"]) == len(mcp["content"]) == 80
    assert file["tool_status"] == "success"
    assert mcp["tool_status"] == "error"
    assert mcp["tool_hook_error"] == "hook diagnostic"
    assert observed == [("read_file", "leading"), ("read_file", raw), ("mcp__mock__logs", raw)]
    store = store_for(cfg, agent.session_id)
    for result in (file, mcp):
        metadata, tail = store.read(result["tool_artifact_id"], len(raw) - 6, 6)
        assert metadata.size_bytes == len(raw.encode("utf-8"))
        assert tail == raw[-6:]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="exercise the POSIX shell with quoted Python")
async def test_capture_and_storage_failure_are_explicit_and_direct_calls_keep_old_output(tmp_path, monkeypatch):
    agent = Agent(config(tmp_path))
    agent.silent = True
    effects: list[str] = []

    def custom_effect() -> str:
        effects.append("executed")
        return "@" * 400

    agent.tool_registry.register(FunctionTool(custom_effect))
    agent.config.context_offloading = ContextOffloadingConfig(threshold_chars=16, max_artifact_bytes=128)
    command = "printf '%0256d' 0"
    (tmp_path / "large.txt").write_text("X" * 100_000, encoding="utf-8")
    agent.client = RecordingProvider(
        tool_turn(
            request("bash", {"command": command}, id="bash"),
            request("read_file", {"path": "large.txt"}, id="file"),
            request("custom_effect", {}, id="custom"),
        ), _response(content="Done."),
    )
    result = await agent.run_loop([{"role": "user", "content": "Run once."}])
    for tool in tool_messages(result):
        assert tool["tool_status"] == "error"
        assert "Tool ran. Output not saved." in tool["content"]
        assert "128-byte artifact limit" in tool["content"]
        assert "tool_artifact_id" not in tool
    assert effects == ["executed"]
    assert not (agent.config.app_home / "artifacts").exists()
    assert len(await BashTool(tmp_path).execute("printf '%0100000d' 0")) == 50_000
    assert len(await ReadFileTool(tmp_path).execute("large.txt")) == 50_000

    def refuse_publish(*_args):
        raise OSError("write failed")

    agent.config.context_offloading = ContextOffloadingConfig(threshold_chars=16)
    monkeypatch.setattr("src.minibot.core.artifacts.os.replace", refuse_publish)
    agent.client = RecordingProvider(tool_turn(request("custom_effect", {})), _response(content="Done."))
    failed, = tool_messages(await agent.run_loop([{"role": "user", "content": "Run it."}]))
    assert effects == ["executed", "executed"]
    assert failed["tool_status"] == "error"
    assert failed["content"] == "Tool ran. Output not saved. Artifact storage unavailable."
    assert not list((agent.config.app_home / "artifacts").rglob("*.artifact"))
    assert not list((agent.config.app_home / "artifacts").rglob(".pending-*"))


@pytest.mark.asyncio
async def test_retrieval_obeys_permissions_budget_and_runtime_validation(tmp_path):
    cfg = config(tmp_path)
    cfg.llm.max_tool_output_chars = 64
    cfg.trace_dir = tmp_path / "traces"
    cfg.tool_permissions = ToolPermissionsConfig(
        mode="manual", default="deny", rules={"read_artifact": "allow", "search_artifact": "deny"},
    )
    cfg.runtime_budget = RuntimeBudgetConfig(max_tool_calls=3)
    agent = Agent(cfg)
    agent.silent = True
    artifact = store_for(cfg, agent.session_id).save("allowed to read", "earlier-run", "source")
    agent.client = RecordingProvider(tool_turn(
        request("search_artifact", {"artifact_id": artifact.artifact_id, "query": "allowed"}, id="deny"),
        request("read_artifact", {"artifact_id": "../outside", "limit": 1}, id="invalid-id"),
        request("read_artifact", {"artifact_id": artifact.artifact_id, "limit": 65}, id="invalid-limit"),
        request("read_artifact", {"artifact_id": artifact.artifact_id, "limit": 64}, id="budget"),
    ))
    messages = [{"role": "user", "content": "Read the artifact."}]
    with pytest.raises(RuntimeBudgetExceeded, match="max_tool_calls"):
        await agent.run_loop(messages)
    statuses = [message["tool_status"] for message in tool_messages(messages)]
    assert statuses == ["refused", "invalid_arguments", "invalid_arguments", "refused"]
    run, = records(cfg.trace_dir)
    assert not any(entry["event"].startswith("artifact_") for entry in run)
    assert run[-1]["status"] == "budget_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", ["code", "explore"])
async def test_task_artifacts_use_child_trace_and_keep_existing_acl(tmp_path, agent_type):
    cfg = config(tmp_path)
    cfg.llm.max_tool_output_chars = 64
    cfg.trace_dir = tmp_path / "traces"
    agent = Agent(cfg)
    agent.silent = True
    (tmp_path / "logs.txt").write_text("x" * 120_000 + "\nTAIL", encoding="utf-8")

    def sub_read(messages):
        item = messages[-1]
        return tool_turn(request("read_artifact", {"artifact_id": item["tool_artifact_id"], "offset": 120_001}, id="read"))

    child = RecordingProvider(
        tool_turn(request("read_file", {"path": "logs.txt"})), sub_read, _response(content="Child completed."),
    )
    agent.subagent_executor.client = child
    agent.client = RecordingProvider(
        tool_turn(request("Task", {"description": "Inspect logs", "prompt": "Open logs.", "agent_type": agent_type})),
        _response(content="Done."),
    )
    await agent.run_loop([{"role": "user", "content": "Delegate the logs."}])
    output = child.requests[-1]["messages"][-1]["content"]
    offered = {tool["function"]["name"] for tool in child.requests[-1]["tools"]}
    run, = records(cfg.trace_dir)
    created, = [entry for entry in run if entry["event"] == "artifact_created"]
    assert created["artifact_run_id"] == created["child_run_id"]
    assert created["artifact_run_id"] != created["run_id"]
    if agent_type == "code":
        assert output == "TAIL"
        assert "read_artifact" in offered
        read, = [entry for entry in run if entry["event"] == "artifact_read"]
        assert read["child_run_id"] == created["child_run_id"]
    else:
        assert output == "Tool 'read_artifact' is not available to this subagent."
        assert "read_artifact" not in offered
        assert not any(entry["event"] == "artifact_read" for entry in run)
    assert created["artifact_id"] in agent.client.requests[-1]["system"]
    assert created["artifact_id"] not in child.requests[-1]["system"]
