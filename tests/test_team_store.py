import pytest

from src.minibot.teams.store import TeamStore


@pytest.mark.asyncio
async def test_team_store_basic_flow(tmp_path):
    store = TeamStore(tmp_path / "team-logs")
    team = await store.create_team(
        team_name="demo",
        lead_id="lead",
        teammate_ids=["member-1", "member-2"],
    )
    assert team.team_id.startswith("team-")

    mailbox = await store.register_mailbox("member-1")
    assert mailbox is not None

    msg = await store.add_message(
        sender_id="lead",
        recipient_id="member-1",
        content="hello",
        kind="direct",
    )
    assert msg.sender_id == "lead"
    await store.enqueue_mail("member-1", {"kind": "message", "content": "hi"})
    item = await mailbox.get()
    assert item["kind"] == "message"

    task = await store.create_task(
        title="task-a",
        details="do work",
        created_by="lead",
    )
    assert task.title == "task-a"
    assigned = await store.assign_task(task.task_id, "member-1")
    assert assigned.assignee_id == "member-1"

    claimed = await store.claim_task("member-1", task.task_id)
    assert claimed is not None
    completed = await store.complete_task(
        task_id=task.task_id,
        completed_by="member-1",
        note="done",
    )
    assert completed.status.value == "completed"

    ok, owner = await store.acquire_file_lock(path="/tmp/a.txt", member_id="member-1")
    assert ok and owner is None
    ok, owner = await store.acquire_file_lock(path="/tmp/a.txt", member_id="member-2")
    assert not ok and owner == "member-1"
    await store.release_member_locks("member-1")
    ok, owner = await store.acquire_file_lock(path="/tmp/a.txt", member_id="member-2")
    assert ok and owner is None

    events = await store.wait_events(timeout_sec=1, max_events=50)
    assert events


@pytest.mark.asyncio
async def test_team_store_lock_requires_active_team(tmp_path):
    store = TeamStore(tmp_path / "team-logs")

    with pytest.raises(ValueError):
        await store.acquire_file_lock(path="/tmp/a.txt", member_id="member-1")

    with pytest.raises(ValueError):
        await store.acquire_workspace_lock(member_id="member-1")
