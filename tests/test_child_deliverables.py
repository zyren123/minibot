from dataclasses import replace
from hashlib import sha256
import os

import pytest

from src.minibot.core.artifacts import ArtifactError, ArtifactStore
from src.minibot.core.run_store import ChildRun, RunState
from src.minibot.core.workspace import WorkspaceIdentity
from src.minibot.subagents.deliverables import (
    apply_child_artifact, export_child_artifacts, parse_deliverable_paths, read_child_artifact,
)


@pytest.mark.parametrize("value", [
    None, "file.py", ["file.py", "file.py"], ["/file.py"], ["./file.py"], ["a//file.py"], ["a/../file.py"],
    ["a\\file.py"], ["a\x00"], ["."], [123], [str(index) for index in range(33)],
])
def test_deliverables_accept_only_unique_canonical_paths(value):
    with pytest.raises(ValueError):
        parse_deliverable_paths(value)


@pytest.mark.asyncio
async def test_child_deliverable_is_verified_and_reused_after_reopen(tmp_path):
    contents = "初稿\r\n尽头\r\n"
    payload = contents.encode("utf-8")
    sessions_dir = tmp_path / "sessions"
    root = tmp_path / "child"
    identity = WorkspaceIdentity.from_root(root, "docker")
    run = RunState(
        run_id="child-run", workspace_id=identity.value, session_id="child-session", sessions_dir=str(sessions_dir),
        status="completed", segment=1, created_at="", updated_at="", parent_run_id="parent-run",
        owner_pid=None, pause_requested=False, wait_reason=None, error=None,
    )
    child = ChildRun(run, "task-step", root, {})

    class Workspace:
        async def read_file(self, path, *, limit, max_bytes):
            assert path == "src/文件.py"
            assert limit is None and max_bytes == 1024
            return contents

    backend = Workspace()
    backend.identity = identity
    path = parse_deliverable_paths(["src/文件.py"])
    app = tmp_path / "app"
    first = ArtifactStore(app, root, sessions_dir, run.session_id, 1024, workspace_id=identity.value)
    reopened = ArtifactStore(app, root, sessions_dir, run.session_id, 1024, workspace_id=identity.value)
    [entry] = await export_child_artifacts(child, backend, first, path)
    assert await export_child_artifacts(child, backend, reopened, path) == (entry,)
    assert entry.path == "src/文件.py"
    assert entry.sha256 == sha256(payload).hexdigest()
    assert entry.size_bytes == len(payload)
    assert read_child_artifact(child, reopened, entry).encode("utf-8") == payload
    assert [metadata.artifact_id for metadata in reopened.recent()] == [entry.artifact_id]

    another_run = replace(child, run=replace(run, run_id="another-child"))
    with pytest.raises(ArtifactError):
        read_child_artifact(another_run, reopened, entry)
    with pytest.raises(ArtifactError):
        read_child_artifact(child, reopened, replace(entry, path="other.py"))
    with pytest.raises(ArtifactError):
        read_child_artifact(child, reopened, replace(entry, sha256="0" * 64))

    [metadata] = reopened.recent()
    record = reopened.directory / metadata.location
    header, original = record.read_bytes().split(b"\n", 1)
    assert original == payload
    record.write_bytes(header + b"\n" + b"x" * len(payload))
    with pytest.raises(ArtifactError):
        read_child_artifact(child, reopened, entry)
    with pytest.raises(ArtifactError):
        await export_child_artifacts(child, backend, reopened, path)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory handles")
def test_apply_child_deliverable_is_explicit_and_never_clobbers(tmp_path):
    workdir = tmp_path / "parent"
    workdir.mkdir()
    nested = workdir / "src"
    nested.mkdir()
    destination = nested / "文件.py"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "result.py"
    outside_file.write_text("outside", encoding="utf-8")
    (workdir / "linked").symlink_to(outside, target_is_directory=True)
    (nested / "linked.py").symlink_to(outside_file)

    contents = "代码\r\n"
    assert apply_child_artifact(workdir, "src/文件.py", contents)
    original_inode = destination.stat().st_ino
    assert not apply_child_artifact(workdir, "src/文件.py", contents)
    assert destination.stat().st_ino == original_inode
    assert destination.read_bytes() == contents.encode("utf-8")
    for path in ("src/文件.py", "linked/result.py", "src/linked.py"):
        with pytest.raises(ArtifactError):
            apply_child_artifact(workdir, path, "other")
    with pytest.raises(ValueError):
        apply_child_artifact(workdir, "../outside/result.py", "other")
    assert destination.read_bytes() == contents.encode("utf-8")
    assert outside_file.read_text(encoding="utf-8") == "outside"
