"""Drive checkpointed file tools in real, dirty Git worktrees."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading

import pytest

from src.minibot.config.schema import Config, HooksConfig, LLMConfig, MCPConfig, MemoryConfig, SessionConfig
from src.minibot.file_checkpoints import FileCheckpointError, GitFileCheckpoints
from src.minibot.main import repl
from src.minibot.sdk import Minibot
from tests.test_tool_outcomes import TurnClient, call


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")


def git(worktree: Path, *args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.check_output(["git", "-C", str(worktree), *args], env=env)


@pytest.fixture
def worktree(tmp_path):
    path = (tmp_path / "worktree").resolve()
    path.mkdir()
    git(path, "init", "--quiet")
    return path


def bot_for(worktree, app_home, *, enabled=True):
    return Minibot(config=Config(
        workdir=worktree,
        app_home=app_home,
        file_checkpoints=enabled,
        llm=LLMConfig(api_key="test", stream_enabled=False),
        hooks=HooksConfig(enabled=False),
        mcp=MCPConfig(enabled=False),
        memory=MemoryConfig(enabled=False),
        session=SessionConfig(enabled=False),
    ))


class FileToolClient(TurnClient):
    async def close(self):
        pass


async def run_file_tool(bot, name, **arguments):
    bot.agent.client = FileToolClient([call(name, json.dumps(arguments))])
    messages = (await bot.chat("Change a file")).messages
    return next(message for message in reversed(messages) if message["role"] == "tool")


@pytest.mark.asyncio
async def test_checkpoint_restores_exact_dirty_bytes_and_mode_without_touching_index(worktree, tmp_path, monkeypatch):
    target = worktree / "笔记.txt"
    other = worktree / "other.txt"
    target.write_bytes(b"committed\n")
    other.write_bytes(b"committed other\n")
    git(worktree, "add", "--", target.name, other.name)
    git(worktree, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Initial files")
    target.write_bytes(b"staged\n")
    git(worktree, "add", "--", target.name)
    index = (worktree / ".git" / "index").read_bytes()
    dirty = "原始内容\r\nsecond\n".encode("utf-8")
    target.write_bytes(dirty)
    other.write_bytes(b"working other\n")
    target.chmod(0o751)
    original_mode = stat.S_IMODE(target.stat().st_mode)
    refs = git(worktree, "for-each-ref")

    # Misleading inherited Git settings must not redirect either repo detection or snapshot storage.
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.setenv(variable, str(tmp_path / "wrong"))
    app_home = tmp_path / "app"
    bot = bot_for(worktree, app_home)
    result = await run_file_tool(bot, "edit_file", path=target.name, old_text="原始内容", new_text="修改过")
    assert result["tool_status"] == "success"
    assert target.read_text(encoding="utf-8") == "修改过\nsecond\n"

    reopened = bot_for(worktree, app_home)
    checkpoint, = await reopened.list_file_checkpoints()
    assert checkpoint.path == target.name
    diff = await reopened.diff_file_checkpoint(checkpoint.id)
    assert target.name in diff and "+修改过" in diff and "-原始内容" in diff
    assert await reopened.rollback_file_checkpoint(checkpoint.id) is True
    assert target.read_bytes() == dirty
    assert stat.S_IMODE(target.stat().st_mode) == original_mode
    assert other.read_bytes() == b"working other\n"
    assert (worktree / ".git" / "index").read_bytes() == index
    assert refs and git(worktree, "for-each-ref") == refs
    if os.name != "nt":
        assert not (app_home / "file-checkpoints").stat().st_mode & 0o077


@pytest.mark.asyncio
async def test_new_file_rollback_consumes_checkpoint_and_leaves_other_files(worktree, tmp_path):
    bot = bot_for(worktree, tmp_path / "app")
    result = await run_file_tool(bot, "write_file", path="new/notes.txt", content="new content\n")
    assert result["tool_status"] == "success"
    path = worktree / "new" / "notes.txt"
    sibling = path.parent / "unrelated"
    sibling.write_bytes(b"mine")
    checkpoint, = await bot.list_file_checkpoints()
    assert checkpoint.created_file
    assert "--- /dev/null" in await bot.diff_file_checkpoint(checkpoint.id)
    assert await bot.rollback_file_checkpoint(checkpoint.id) is True
    assert not path.exists() and sibling.read_bytes() == b"mine"
    assert (await bot.list_file_checkpoints())[0].rolled_back

    path.write_bytes(b"new content\n")
    assert await bot.rollback_file_checkpoint(checkpoint.id) is False
    assert path.read_bytes() == b"new content\n"


@pytest.mark.asyncio
async def test_rollback_refuses_a_later_edit_or_replaced_target(worktree, tmp_path):
    target = worktree / "notes.txt"
    target.write_bytes(b"original")
    bot = bot_for(worktree, tmp_path / "app")
    assert (await run_file_tool(bot, "write_file", path=target.name, content="agent"))["tool_status"] == "success"
    checkpoint, = await bot.list_file_checkpoints()
    target.write_bytes(b"later user edit")
    with pytest.raises(FileCheckpointError, match="conflict"):
        await bot.rollback_file_checkpoint(checkpoint.id)
    assert target.read_bytes() == b"later user edit"
    assert not (await bot.list_file_checkpoints())[0].rolled_back

    if os.name != "nt":
        original = tmp_path / "outside"
        original.write_bytes(b"agent")
        target.unlink()
        target.symlink_to(original)
        with pytest.raises(FileCheckpointError, match="regular path"):
            await bot.rollback_file_checkpoint(checkpoint.id)
        assert target.is_symlink() and original.read_bytes() == b"agent"


@pytest.mark.asyncio
async def test_interrupted_rollback_remains_visible_and_cannot_be_replayed(worktree, tmp_path, monkeypatch):
    target = worktree / "notes.txt"
    target.write_bytes(b"original")
    bot = bot_for(worktree, tmp_path / "app")
    assert (await run_file_tool(bot, "write_file", path=target.name, content="agent"))["tool_status"] == "success"
    checkpoint, = await bot.list_file_checkpoints()
    original_git = GitFileCheckpoints._git

    def failing_finalize(store, *args, **kwargs):
        if args == ("update-ref", "--stdin"):
            raise FileCheckpointError("Simulated interruption before rollback completion")
        return original_git(store, *args, **kwargs)

    monkeypatch.setattr(GitFileCheckpoints, "_git", failing_finalize)
    with pytest.raises(FileCheckpointError, match="Simulated interruption"):
        await bot.rollback_file_checkpoint(checkpoint.id)
    assert target.read_bytes() == b"original"
    target.write_bytes(b"agent")
    recorded, = await bot.list_file_checkpoints()
    assert recorded.status == "rollback_incomplete"
    with pytest.raises(FileCheckpointError, match="previous rollback was interrupted"):
        await bot.rollback_file_checkpoint(checkpoint.id)
    assert target.read_bytes() == b"agent"
    assert "+agent" in await bot.diff_file_checkpoint(checkpoint.id)


@pytest.mark.asyncio
async def test_failed_capture_does_not_change_files_or_create_target_parents(worktree, tmp_path, monkeypatch):
    bot = bot_for(worktree, tmp_path / "app")
    target = worktree / "one.txt"
    target.write_bytes(b"original")
    original_git = GitFileCheckpoints._git

    def failing_save(store, *args, **kwargs):
        if args[0] == "mktree":
            raise FileCheckpointError("Simulated journal failure")
        return original_git(store, *args, **kwargs)

    monkeypatch.setattr(GitFileCheckpoints, "_git", failing_save)
    for path in ("one.txt", "new/notes.txt"):
        result = await run_file_tool(bot, "write_file", path=path, content="unrecorded")
        assert result["tool_status"] == "error"
        assert "Simulated journal failure" in result["content"]
    assert target.read_bytes() == b"original"
    assert not (worktree / "new").exists()
    assert await bot.list_file_checkpoints() == []


@pytest.mark.asyncio
async def test_disabled_checkpoints_preserve_normal_non_git_writes(tmp_path):
    bot = bot_for(tmp_path, tmp_path / "app", enabled=False)
    result = await run_file_tool(bot, "write_file", path="notes.txt", content="ordinary")
    assert result["tool_status"] == "success"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "ordinary"
    assert not (tmp_path / "app" / "file-checkpoints").exists()
    with pytest.raises(FileCheckpointError, match="disabled"):
        await bot.list_file_checkpoints()


@pytest.mark.asyncio
async def test_cancelled_file_write_finishes_its_checkpoint_transaction(worktree, tmp_path, monkeypatch):
    bot = bot_for(worktree, tmp_path / "app")
    entered = threading.Event()
    proceed = threading.Event()
    original_git = GitFileCheckpoints._git

    def delayed_save(store, *args, **kwargs):
        if args[0] == "mktree":
            entered.set()
            assert proceed.wait(5)
        return original_git(store, *args, **kwargs)

    monkeypatch.setattr(GitFileCheckpoints, "_git", delayed_save)
    tool = bot.agent.tool_registry.get("write_file")
    assert tool is not None
    task = asyncio.create_task(tool.execute_result(path="notes.txt", content="recorded"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    checkpoint, = await bot.list_file_checkpoints()
    assert checkpoint.path == "notes.txt"
    assert await bot.rollback_file_checkpoint(checkpoint.id) is True
    assert not (worktree / "notes.txt").exists()


@pytest.mark.asyncio
async def test_repl_can_list_preview_and_confirm_rollback(worktree, tmp_path, monkeypatch, capsys):
    bot = bot_for(worktree, tmp_path / "app")
    result = await run_file_tool(bot, "write_file", path="notes.txt", content="new content")
    assert result["tool_status"] == "success"
    checkpoint, = await bot.list_file_checkpoints()
    commands = iter([
        "/checkpoint list", f"/checkpoint diff {checkpoint.id}",
        f"/checkpoint rollback {checkpoint.id}", "no",
        f"/checkpoint rollback {checkpoint.id}", "yes", "exit",
    ])
    monkeypatch.setattr("src.minibot.main.prompt_toolkit_enabled", lambda: False)
    monkeypatch.setattr("src.minibot.main.rich_enabled", lambda: False)
    monkeypatch.setattr("src.minibot.main.prompt_input", lambda _: next(commands))
    await repl(bot.agent)
    output = capsys.readouterr().out
    assert checkpoint.id in output and "+new content" in output
    assert "Rollback canceled." in output and "File restored." in output
    assert not (worktree / "notes.txt").exists()
