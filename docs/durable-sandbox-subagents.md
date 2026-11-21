# Durable sandbox subagents

Enable durability on a trusted local parent. Install a local Docker daemon
and make the configured child image available on that daemon. The image
needs `/bin/sh` and `python3`. MiniBot records its image ID when it creates
a child.

```yaml
durable_execution: true
workspace:
  backend: local
durable_child_workspace:
  image: python:3.12-slim
  timeout: 60
```

When the parent calls `Task`, MiniBot commits the Child Run, its session,
the workspace identity, and the parent step in one SQLite transaction.
The child's workspace starts empty. Provide input in the prompt and list
the files the child should deliver in the optional `deliverables` array.
The child uses the parent's model connection and execution budget. It
runs file and Bash tools in its own Docker mount with networking disabled
and gets only the sandbox tools permitted for its agent type.

Use `TaskBatch` for up to four independent jobs. Its `tasks` array uses the
same fields as `Task`. Each job has its own workspace, and receipts follow
the input order. Place dependent jobs in later model turns so the parent
can inspect the earlier results before delegating. Non-durable `Task`
continues to use the existing subagent executor.

## Resume and operator recovery

Open the same parent workspace, app home, and session after a restart.
The parent run stays in `waiting_child` while its delegation step is
unfinished. Resume the parent to recover the original child and reuse any
finished result or committed receipt. Child listing reports the last
committed state. Resume verifies Docker cleanup before changing that state.

```python
for run in bot.list_runs(incomplete_only=True):
    for child in bot.list_child_runs(run.run_id):
        print(child.run.run_id, child.run.status, child.parent_step_id)
    result = await bot.resume_run(run.run_id)
```

`pause_run(parent_run_id)` signals the active children to stop and awaits
their cleanup as part of the parent execution. To stop one active child, call
`pause_child_run(parent_run_id, child_run_id)`. Both methods record a request.
Wait for the active execution to return before starting a new segment.

If an interrupted tool could have changed state, inspect it using
`list_child_run_steps(parent_run_id, child_run_id)` and verify the actual
effect. Record the result with
`await bot.resolve_child_run_step(parent_run_id, child_run_id, step_id, output)`
before resuming the parent. Use `await bot.abandon_child_run(parent_run_id,
child_run_id)` to end a stopped child after its workspace and Docker cleanup
have been verified. Finish or abandon unresolved children before abandoning
their parent.

MiniBot compares the recorded workspace identity and the local Docker daemon
before resuming execution. A replaced workspace or unverifiable cleanup
blocks execution. Restore the original workspace or daemon and retry
attachment. If a Docker create request may still be in flight and MiniBot
has no matching container or confirmed ID, the child stays blocked until
cleanup can be verified.

## Deliver and install files

Each successful export provides a workspace-relative path, SHA-256, byte
length, and Artifact ID. The parent model can use `ReadChildArtifact` during
the delegating run. SDK callers can inspect and verify the same artifact
independently of the running Docker container.

```python
for artifact in bot.list_child_artifacts(parent_run_id, child_run_id):
    text = bot.read_child_artifact(parent_run_id, child_run_id, artifact.path)
    print(artifact.sha256, text)

installed = bot.apply_child_artifact(
    parent_run_id, child_run_id, "report.txt", "reports/child-report.txt",
)
```

Exports support regular UTF-8 files within the configured artifact size
limit. Applying an artifact is an explicit POSIX SDK operation and requires
an existing destination directory. It creates a file atomically, returns
`False` if the destination already contains identical bytes, and rejects
conflicting data or symlinks. For edits to an existing file, read the
verified artifact and review the changes before editing the parent workspace.

The offline model and real Docker process tests can be run with
`MINIBOT_TEST_DOCKER=1 python -m pytest tests/test_durable_sandbox_child.py tests/test_durable_child_batch.py`.
