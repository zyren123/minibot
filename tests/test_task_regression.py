"""Run fixed tasks through MiniBot with a scripted model and real effects."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from src.minibot.config.schema import HookDefinition, HooksConfig
from src.minibot.core.client import LLMClient
from src.minibot.sdk import Minibot
from src.minibot.server.app import create_app
from tests.task_regression import ExpectedTask, Segment, ToolStep, evaluate_task
from tests.test_agent_streaming import FakeStream, _chunk, _response
from tests.test_bash_interrupt import config
from tests.test_tool_outcomes import call


class ScriptedProvider:
    model = "task-regression"

    def __init__(self, *responses):
        self.responses = iter(responses)

    async def create_message_async(self, *, messages, **kwargs):
        try:
            response = next(self.responses)
        except StopIteration:
            raise AssertionError("Unexpected model request after the scripted turns") from None
        return response(messages) if callable(response) else response

    async def create_message_stream_async(self, **kwargs):
        response = await self.create_message_async(**kwargs)
        choice = response.choices[0]
        deltas = [
            {"index": index, "id": tool.id, "function": tool.function}
            for index, tool in enumerate(choice.message.tool_calls)
        ]
        return FakeStream([_chunk(
            content=choice.message.content, tool_calls=deltas, finish_reason=choice.finish_reason,
        )])


def tool_turn(*calls):
    return _response(content="", finish_reason="tool_calls", tool_calls=list(calls))


def note_call(name, content):
    return call(name, json.dumps({"content": content}))


def record_operation(workspace: Path, operation: str) -> None:
    with (workspace / "operations.log").open("a", encoding="utf-8") as journal:
        journal.write(f"{operation}\n")


def note_tools(workspace: Path):
    note = workspace / "note.txt"

    def write_note(content: str) -> str:
        record_operation(workspace, "write_note")
        note.write_text(content, encoding="utf-8", newline="\n")
        return "Note written."

    def append_note(content: str) -> str:
        record_operation(workspace, "append_note")
        with note.open("a", encoding="utf-8", newline="\n") as file:
            file.write(content)
        return "Note updated."

    def fail_note() -> str:
        record_operation(workspace, "fail_note")
        raise OSError("Fixture write failed")

    return [write_note, append_note, fail_note]


def make_bot(workspace, *responses, hooks=None):
    cfg = config(workspace, backend="disabled")
    cfg.trace_dir = workspace / "traces"
    if hooks is not None:
        cfg.hooks = hooks
    bot = Minibot(config=cfg, tools=note_tools(workspace))
    bot.agent.client = ScriptedProvider(*responses)
    return bot


def assert_task(workspace, session_id, expected):
    failures = evaluate_task(workspace, workspace / "traces", session_id, expected)
    if failures:
        pytest.fail("\n".join(failures), pytrace=False)


FILE_TASK = ExpectedTask(
    segments=(Segment((ToolStep("write_note"), ToolStep("append_note"))),),
    operations=("write_note", "append_note"),
    files={"note.txt": "Draft\nReviewed\n"},
)


async def run_file_task(workspace):
    bot = make_bot(
        workspace,
        tool_turn(note_call("write_note", "Draft\n")),
        tool_turn(note_call("append_note", "Reviewed\n")),
        _response(content="Done."),
    )
    await asyncio.wait_for(bot.chat("Create and review the note."), timeout=5)
    return bot.session_id


@pytest.mark.asyncio
async def test_successful_file_task(tmp_path):
    session_id = await run_file_task(tmp_path)
    assert_task(tmp_path, session_id, FILE_TASK)


async def refuse_note_write(context):
    return {"blocked": context.tool_name == "write_note", "block_reason": "Note is locked."}


@pytest.mark.asyncio
async def test_hook_refusal(tmp_path):
    hooks = HooksConfig(hooks=[HookDefinition("pre_tool_call", f"{__name__}:refuse_note_write")])
    bot = make_bot(
        tmp_path, tool_turn(note_call("write_note", "Forbidden\n")), _response(content="Stopped."),
        hooks=hooks,
    )
    await asyncio.wait_for(bot.chat("Try writing the locked note."), timeout=5)
    assert_task(tmp_path, bot.session_id, ExpectedTask(
        segments=(Segment((ToolStep("write_note", "refused", is_error=True),)),),
        operations=(),
        files={"note.txt": None},
    ))


@pytest.mark.asyncio
async def test_tool_failure_with_fallback(tmp_path):
    def recover(messages):
        assert messages[-1]["role"] == "tool"
        assert "Fixture write failed" in messages[-1]["content"]
        assert not (tmp_path / "note.txt").exists(), "failed write left an artifact before fallback"
        return tool_turn(note_call("write_note", "Fallback\n"))

    bot = make_bot(tmp_path, tool_turn(call("fail_note")), recover, _response(content="Recovered."))
    await asyncio.wait_for(bot.chat("Try the primary write, then use the fallback."), timeout=5)
    assert_task(tmp_path, bot.session_id, ExpectedTask(
        segments=(Segment((ToolStep("fail_note", "error", is_error=True), ToolStep("write_note"))),),
        operations=("fail_note", "write_note"),
        files={"note.txt": "Fallback\n"},
    ))


@pytest.mark.asyncio
async def test_user_cancellation(tmp_path):
    started, cleaning, finish_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def hold_note():
        record_operation(tmp_path, "hold_note")
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaning.set()
            await finish_cleanup.wait()
            record_operation(tmp_path, "finalize_note")

    bot = make_bot(tmp_path, tool_turn(
        call("hold_note"), call("write_note", '{"content":"Unexpected"}', id="later"),
    ))
    bot.register_tool(hold_note)
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        running = tasks.create_task(bot.chat("Hold the note until cancelled."))
        try:
            await started.wait()
            running.cancel()
            await cleaning.wait()
            paths = list((tmp_path / "traces").glob("*.jsonl"))
            assert len(paths) == 1, f"Unusable trace during cleanup: found {len(paths)} segments"
            path, = paths
            events = [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()]
            assert events == ["run_start", "tool_start"], "Trace closed before the tool finalized"
            assert not running.done(), "SDK call finished during tool cleanup"
        finally:
            finish_cleanup.set()
    assert running.cancelled(), "SDK call did not propagate cancellation"
    assert_task(tmp_path, bot.session_id, ExpectedTask(
        segments=(Segment((ToolStep("hold_note", "interrupted", is_error=True),), "interrupted"),),
        operations=("hold_note", "finalize_note"),
        files={"note.txt": None},
    ))


@pytest.mark.asyncio
async def test_persisted_question_resumes_after_server_reload(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text(
        "trace_dir: ../traces\n"
        "llm: {model: task-regression, api_key: fixture, base_url: 'https://example.invalid/v1'}\n"
        "memory: {enabled: false}\n"
        "teams: {enabled: false}\n"
        "tools: {bash: {backend: disabled}}\n",
        encoding="utf-8",
    )
    (config_dir / "hooks.yaml").write_text("enabled: false\n", encoding="utf-8")
    (config_dir / "mcp_servers.yaml").write_text("enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("MINIBOT_HOME", str(home))
    plugin = tmp_path / "note_plugin.py"
    plugin.write_text(
        "from pathlib import Path\n"
        f"from {__name__} import note_tools\n"
        "TOOLS = note_tools(Path(__file__).parent)\n",
        encoding="utf-8",
    )

    def amend(messages):
        answer = messages[-1]
        assert answer["role"] == "user"
        return tool_turn(note_call("append_note", answer["content"] + "\n"))

    provider = ScriptedProvider(
        tool_turn(note_call("write_note", "Draft\n")),
        tool_turn(call("askuserquestion", '{"prompt":"Which line should I add?"}', id="note-question")),
        amend,
        _response(content="Updated."),
    )

    async def stream_response(client, **kwargs):
        return await provider.create_message_stream_async(**kwargs)

    async def chat_response(client, **kwargs):
        return await provider.create_message_async(**kwargs)

    monkeypatch.setattr(LLMClient, "create_message_stream_async", stream_response)
    monkeypatch.setattr(LLMClient, "create_message_async", chat_response)

    paused_segment = Segment((
        ToolStep("write_note"), ToolStep("askuserquestion"), "run_waiting",
    ), "waiting_for_user")
    async with asyncio.timeout(10):
        transport = httpx.ASGITransport(app=create_app(workdir=tmp_path))
        async with httpx.AsyncClient(transport=transport, base_url="http://minibot.test") as client:
            bot = await client.post("/api/bots", json={"name": "Task regression"})
            bot_path = f"/api/bots/{bot.raise_for_status().json()['bot_id']}"
            settings = await client.put(f"{bot_path}/config", json={"tool_plugins": [str(plugin)]})
            settings.raise_for_status()
            session = await client.post(f"{bot_path}/sessions")
            session_id = session.raise_for_status().json()["session_id"]
            session_path = f"{bot_path}/sessions/{session_id}"
            paused = await client.post(f"{bot_path}/stream", json={
                "session_id": session_id, "prompt": "Create a draft, ask for a line, and append it.",
            })
            assert "event: ask_user_question" in paused.raise_for_status().text
            assert_task(tmp_path, session_id, ExpectedTask(
                segments=(paused_segment,), operations=("write_note",), files={"note.txt": "Draft\n"},
            ))

        restarted = httpx.ASGITransport(app=create_app(workdir=tmp_path))
        async with httpx.AsyncClient(transport=restarted, base_url="http://minibot.test") as client:
            pending = await client.get(f"{session_path}/pending-question")
            question = pending.raise_for_status().json()
            assert question and question["question_id"] == "note-question"
            answer = await client.post(f"{session_path}/answer/stream", json={
                "question_id": question["question_id"], "answer_text": "已确认",
            })
            assert "event: tool_result" in answer.raise_for_status().text
            cleared = await client.get(f"{session_path}/pending-question")
            assert cleared.raise_for_status().json() is None

    assert_task(tmp_path, session_id, ExpectedTask(
        segments=(paused_segment, Segment((ToolStep("append_note"),))),
        operations=("write_note", "append_note"),
        files={"note.txt": "Draft\n已确认\n"},
    ))


@pytest_asyncio.fixture
async def captured_file_task(tmp_path):
    session_id = await run_file_task(tmp_path)
    assert_task(tmp_path, session_id, FILE_TASK)
    path, = (tmp_path / "traces").glob("*.jsonl")
    return session_id, path


def write_trace_rows(path, rows):
    for sequence, row in enumerate(rows, 1):
        row["sequence"] = sequence
    path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "replacement", "diagnostic"), [
    pytest.param("note.txt", "Wrong contents\n", "Artifact note.txt: expected", id="wrong-artifact"),
    pytest.param("note.txt", "Draft\r\nReviewed\r\n", "Artifact note.txt: expected", id="wrong-line-endings"),
    pytest.param("operations.log", "write_note\nappend_note\nwrite_note\n", "Operations: expected", id="extra-effect"),
])
async def test_evaluation_fails_for_wrong_effects(tmp_path, captured_file_task, name, replacement, diagnostic):
    session_id, _ = captured_file_task
    (tmp_path / name).write_text(replacement, encoding="utf-8", newline="\n")
    with pytest.raises(pytest.fail.Exception, match=diagnostic):
        assert_task(tmp_path, session_id, FILE_TASK)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tail", "diagnostic"), [
    pytest.param(None, "no JSONL segments found", id="missing-file"),
    pytest.param("", "missing terminal run_end", id="missing-terminal"),
    pytest.param('{"event": "run_', "malformed JSON", id="truncated-json"),
    pytest.param("[]\n", "expected a JSON object", id="non-object"),
])
async def test_evaluation_fails_for_unusable_trace(tmp_path, captured_file_task, tail, diagnostic):
    session_id, path = captured_file_task
    if tail is None:
        path.unlink()
    else:
        prefix = path.read_text(encoding="utf-8").splitlines(keepends=True)[:-1]
        path.write_text("".join(prefix) + tail, encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=f"Unusable trace: .*{diagnostic}"):
        assert_task(tmp_path, session_id, FILE_TASK)


@pytest.mark.asyncio
@pytest.mark.parametrize(("row_index", "fields", "diagnostic"), [
    pytest.param(0, {"version": 2}, "Unusable trace: .*unsupported version 2", id="unsupported-version"),
    pytest.param(0, {"parent_run_id": "unrelated"}, "Unusable trace: .*missing root segment", id="orphan-segment"),
    pytest.param(1, {"session_id": "another-session"}, "Unusable trace: .*identity mismatch", id="wrong-session"),
    pytest.param(2, {"invocation_id": uuid4().hex}, "Unusable trace: .*does not match", id="unpaired-invocation"),
    pytest.param(2, {"status": "refused", "is_error": True}, r"steps: .*got write_note\(refused", id="wrong-tool-status"),
    pytest.param(-1, {"status": "failed"}, "terminal status: expected completed, got failed", id="wrong-run-status"),
])
async def test_evaluation_fails_for_wrong_trace_record(tmp_path, captured_file_task, row_index, fields, diagnostic):
    session_id, path = captured_file_task
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[row_index].update(fields)
    write_trace_rows(path, rows)
    with pytest.raises(pytest.fail.Exception, match=diagnostic):
        assert_task(tmp_path, session_id, FILE_TASK)


@pytest.mark.asyncio
async def test_evaluation_fails_for_wrong_tool_order(tmp_path, captured_file_task):
    session_id, path = captured_file_task
    start, write_start, write_end, append_start, append_end, end = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    original_times = [row["elapsed_ms"] for row in (write_start, write_end, append_start, append_end)]
    reordered = (append_start, append_end, write_start, write_end)
    for row, elapsed in zip(reordered, original_times, strict=True):
        row["elapsed_ms"] = elapsed
    write_trace_rows(path, [start, *reordered, end])
    with pytest.raises(pytest.fail.Exception, match=r"steps: .*got append_note\("):
        assert_task(tmp_path, session_id, FILE_TASK)


@pytest.mark.asyncio
async def test_evaluation_fails_for_extra_invocation(tmp_path, captured_file_task):
    session_id, path = captured_file_task
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    invocation_id = uuid4().hex
    extra = [dict(row, invocation_id=invocation_id, elapsed_ms=rows[-2]["elapsed_ms"]) for row in rows[1:3]]
    write_trace_rows(path, [*rows[:-1], *extra, rows[-1]])
    with pytest.raises(pytest.fail.Exception, match=r"steps: .*got write_note\(.*append_note\(.*write_note\("):
        assert_task(tmp_path, session_id, FILE_TASK)
