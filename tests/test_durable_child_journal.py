"""Verify durable child identity, recovery, and ownership against SQLite."""

from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from src.minibot.core.run_store import DurableRunBusy, DurableRunError, DurableRunPaused, RunStore
from src.minibot.core.workspace import WorkspaceIdentity
from tests.test_durable_execution import wait_for_file


SESSION = "parent-session"
PROMPT = [{"role": "user", "content": "实现 tâche"}]


def parent_store(root):
    return RunStore(root / "home", WorkspaceIdentity("parent-workspace"), root / "sessions")


def plan(journal):
    journal.create(PROMPT, None, None)
    journal.start_segment()
    [step] = journal.plan_batch(PROMPT, {"message_id": "assistant"}, [("tool-call", "Task", "{}")])
    return step


def create_child(journal, step, root, *, ordinal=0, path=None, run_id=None):
    run_id = run_id or uuid4().hex
    return journal.create_child(
        step, child_run_id=run_id, workspace_path=path or root / run_id,
        workspace_identity=WorkspaceIdentity(run_id), execution={"backend": "docker"},
        messages=PROMPT, ordinal=ordinal,
    )


def complete_child(journal):
    journal.start_segment()
    journal.save_messages([*PROMPT, {"role": "assistant", "content": "完成"}], complete=True)
    journal.end_segment("completed")


def checkpoint_worker(root, finish):
    store = parent_store(root)
    with store.claim(SESSION, uuid4().hex, new=True) as parent:
        step = plan(parent)
        child = create_child(parent, step, root)
        with store.for_child(child).claim(child.run.session_id, child.run.run_id) as journal:
            if finish:
                complete_child(journal)
            (root / "ready").write_text(parent.run_id, encoding="utf-8")
            while True:
                time.sleep(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [False, True], ids=["before-execute", "after-child-complete"])
async def test_child_attachment_survives_process_kill(tmp_path, finish):
    child_process = subprocess.Popen([
        sys.executable, "-m", "tests.test_durable_child_journal", str(tmp_path), str(int(finish)),
    ], cwd=Path(__file__).resolve().parents[1])
    try:
        parent_id = await wait_for_file(tmp_path / "ready", child_process)
        store = parent_store(tmp_path)
        [child] = store.list_children(SESSION, parent_id)
        child_scope = store.for_child(child)
        assert child.run.session_id == child.run.run_id != SESSION
        assert child.run.parent_run_id == parent_id
        assert child.run.status == ("completed" if finish else "created")
        with pytest.raises(DurableRunBusy), child_scope.claim(child.run.session_id, child.run.run_id):
            pytest.fail("The child execution lock already has an owner")
    finally:
        child_process.kill()
        child_process.wait(timeout=10)
    assert child_process.returncode != 0

    reopened = parent_store(tmp_path)
    with reopened.claim(SESSION, parent_id) as parent:
        assert parent.recover().status == "waiting_child"
        assert parent.state().owner_pid is None
        [step] = reopened.list_steps(SESSION, parent_id)
        assert step.child_run_id == child.run.run_id and step.status == "waiting_child"
        assert parent.child_for_step(step) == child
        assert create_child(parent, step, tmp_path) == child
        parent.start_segment()
        parent.wait_for_child(step)
        with reopened.for_child(child).claim(child.run.session_id, child.run.run_id) as journal:
            if not finish:
                assert journal.checkpoint().messages == PROMPT
                complete_child(journal)
            result = journal.checkpoint().messages[-1]
            assert result == {"role": "assistant", "content": "完成"}
            assert journal.state().segment == 1
        parent.finish_step(step, {"role": "tool", "content": result["content"]})
        assert parent.state().status == "running"
        assert reopened.list_steps(SESSION, parent_id)[0].result["content"] == "完成"
    assert len(reopened.list_children(SESSION, parent_id)) == 1


def test_child_slots_are_scoped_and_failed_publication_rolls_back(tmp_path):
    store = parent_store(tmp_path)
    with store.claim(SESSION, uuid4().hex, new=True) as parent:
        step = plan(parent)
        first = create_child(parent, step, tmp_path)
        rejected_id = uuid4().hex
        with pytest.raises(sqlite3.IntegrityError):
            create_child(parent, step, tmp_path, path=first.workspace_path, ordinal=1, run_id=rejected_id)
        rejected_scope = RunStore(tmp_path / "home", WorkspaceIdentity(rejected_id), tmp_path / "sessions")
        assert rejected_scope.list_runs(rejected_id) == []
        assert parent.child_for_step(step, ordinal=1) is None

        second = create_child(parent, step, tmp_path, ordinal=1)
        assert store.list_children(SESSION, parent.run_id) == [first, second]
        assert store.get_child(SESSION, parent.run_id, second.run.run_id) == second
        assert parent.child_for_step(step, ordinal=1) == second
        with pytest.raises(DurableRunError, match="workspace and session"):
            store.get_child("other-session", parent.run_id, second.run.run_id)
        with store.claim("other-session", uuid4().hex, new=True) as other:
            plan(other)
            with pytest.raises(DurableRunError, match="parent run"):
                store.get_child("other-session", other.run_id, second.run.run_id)
        assert store.pause(SESSION, parent.run_id)
        with pytest.raises(DurableRunPaused):
            parent.wait_for_child(step)
        parent.end_segment("interrupted")
        assert parent.state().status == "waiting_child"


if __name__ == "__main__":
    checkpoint_worker(Path(sys.argv[1]), bool(int(sys.argv[2])))
