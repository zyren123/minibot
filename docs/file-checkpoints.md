# File checkpoints

File checkpoints let you inspect and undo one file change made by MiniBot. Install Git, start MiniBot in a Git worktree, and enable the feature in your MiniBot YAML:

```yaml
file_checkpoints: true
```

MiniBot records changes made by the built-in `write_file` and `edit_file` tools. Each checkpoint stores the original and updated bytes and permission bits in a private Git repository under `<app_home>/file-checkpoints/`. The repository is shared by sessions using the same workspace and app home. Its history contains copies of file contents, including any secrets in those files.

In the REPL, list checkpoints, preview the recorded change, then choose the checkpoint to restore:

```text
/checkpoint list
/checkpoint diff ID
/checkpoint rollback ID
```

Confirm the rollback when prompted. MiniBot restores the original bytes and permission bits if the file still matches the recorded revision. If the checkpoint created the file, rollback removes it and leaves its parent directories in place. For sequential edits to the same file, roll back the newest change first. The workspace's Git index, branch and unrelated files keep their current state.

The Python SDK enables the same feature with `Config(file_checkpoints=True)`. On the resulting `Minibot` instance, await `list_file_checkpoints()`, `diff_file_checkpoint(id)` or `rollback_file_checkpoint(id)`. Rollback returns `True` after restoring the file and `False` if that checkpoint has already been restored. A missing checkpoint, conflict or interrupted rollback raises `FileCheckpointError`.

Each snapshot supports up to 16 MiB of file data. MiniBot checkpoints canonical workspace paths to regular files with a single hard link. Unchanged writes and changes made through Bash, MCP or custom tools produce no checkpoint. Rollback rejects targets that have been replaced with symbolic links or that no longer match the recorded bytes and mode. Pause other programs that write to the same file while restoring it. MiniBot serializes its own checkpoint operations, and other programs can still race with the final filesystem update.

An abrupt process exit can leave a recorded write whose update never reached the workspace. The normal rollback check rejects it if its post-state is absent. If a rollback is interrupted between recording its start and recording completion, it appears as `rollback_incomplete`. Inspect the current file and the recorded diff before making further changes. MiniBot blocks automatic replay of that checkpoint.
