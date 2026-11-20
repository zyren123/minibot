# Configure a workspace backend

Set `workspace.backend: docker` to run Bash, file reads, writes, edits, and
directory listings in the same Docker workspace. Set the policy in
`$MINIBOT_HOME/config/default.yaml` for the CLI and WebUI, or pass
`WorkspaceConfig` to the SDK.

## Choose the workspace directory

Run MiniBot from an installation and Python environment outside the workspace.
Keep `MINIBOT_HOME`, sessions, and traces outside the workspace as well. Use a
dedicated workspace directory. A Bash command can access everything you put
there, including credentials, hard-linked files, and socket files.

Use a trusted local Docker daemon and image. The engine must support Docker's
`bind-recursive=disabled` mount option, which excludes nested host mounts.
Preload the default image before starting MiniBot.

```sh
docker pull python:3.12-slim
```

A custom image must supply `/bin/sh` and Python 3 with its standard library.
MiniBot rejects images that declare volumes. For a run that needs the same
image after a restart, set `image` to a locally available immutable image ID.

## Enable Docker execution

The following values show the Docker defaults. Change the settings and restart
MiniBot. `workspace.timeout` accepts a positive number of seconds. The default
`null` uses `tools.timeout`.

```yaml
workspace:
  backend: docker
  image: python:3.12-slim
  network: none
  memory_mb: 512
  cpus: 1.0
  pids_limit: 128
  timeout: null
```

Set `network: bridge` to give operations access to Docker's bridge network.
Enable networking only when you want commands to communicate outside the
container. MiniBot forwards no host environment variables or Docker CLI proxy
settings. Environment variables defined in the selected image remain available.

For the SDK, supply the workspace and runtime paths when creating the bot.

```python
from pathlib import Path

from minibot import Minibot
from minibot.config import Config, WorkspaceConfig

runtime = Path("/path/to/minibot-runtime")
bot = Minibot(
    config=Config(
        workdir=Path("/path/to/project"),
        app_home=runtime,
        workspace=WorkspaceConfig(backend="docker"),
    ),
    sessions_dir=runtime / "sessions",
)
```

Every operation starts a fresh container at `/workspace`. The directory is a
read-write bind mount, so files persist across operations. Each container has a
read-only root filesystem, a 64 MiB temporary filesystem at `/tmp`, no Linux
capabilities, and no privilege escalation. On POSIX, the container process
runs as the host user's UID and GID. MiniBot uses the same timeout, cancellation,
and cleanup logic for shell and file operations. A blocked Docker daemon can
extend cleanup past the operation timeout. If cleanup cannot be confirmed,
MiniBot reports the container name in the error or cancellation diagnostic.
If Docker forcibly stops a file write or edit, a `.minibot-write-*` staging
file may remain in the workspace. A committed target is replaced atomically.

Use workspace-relative paths with file tools. For example, Bash can run
`printf hello > results.txt` and `read_file` can then read `results.txt`.
`list_dir(".")` returns the immediate names at the workspace root. File tools
reject absolute paths, parent traversal, symlinks, and nonregular file targets.
Write and edit operations limit file content to 16 MiB. Directory listings
limit a single directory to 1,000 entries.

## Host capabilities and run recovery

Docker workspace mode refuses active hooks, configured MCP connections, and
custom Python tool registrations. The WebUI also refuses configured plugins
and attached subagent bots before loading them. Skills, memory, Team, and Task
are unavailable in this mode. Edit the trusted app-home configuration to
disable any incompatible settings before starting MiniBot. Empty hook and MCP
configurations work with their default enabled settings.

Tool approvals and execution budgets still apply. A file operation that Docker
cannot complete returns an error. All workspace file and shell operations stay
inside Docker.

Keep Git file checkpoints disabled in Docker workspace mode. Git file
checkpoints and rollback are available in trusted local mode. Workspace
snapshots are reserved for a future backend capability. Durable execution
records the workspace ID and requires the same workspace directory and policy
to resume. Keep the directory in place while the agent runs. Replacing the
directory at the same path changes the ID. Artifacts
are linked to that workspace ID and read from the
application runtime directory. [Enable durable execution](durable-execution.md)
and [configure context offloading](context-offloading.md).

## Trusted local mode

The default `workspace.backend: local` runs file tools as the host user.
With the default Bash policy, shell commands run with host access. Use
`tools.bash.backend: docker` for Bash-only isolation, or `disabled` to omit
Bash in either workspace mode. When you opt into `workspace.backend: docker`,
the workspace policy supplies the image and limits for Bash and file
operations. [Configure Bash-only isolation](bash-isolation.md).

## Run Docker verification

With the default image available in a local Docker daemon, run the opt-in
workspace integration tests.

```sh
MINIBOT_TEST_DOCKER=1 uv run --with pytest --with pytest-asyncio pytest tests/test_workspace_docker_integration.py -q
```
