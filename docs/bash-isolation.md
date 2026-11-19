# Configure Bash isolation

Bash runs on the host by default. To run the built-in Bash tool in Docker, prepare a trusted local image and set `tools.bash.backend` to `docker` in your global `config/default.yaml` under the Minibot app home. Restart Minibot after editing this file.

```sh
docker pull debian:bookworm
```

```yaml
tools:
  timeout: 60
  bash:
    backend: docker
    image: debian:bookworm
    memory_mb: 512
    cpus: 1.0
    pids_limit: 128
```

Choose `local` to keep host execution, or `disabled` to omit the built-in Bash tool. Existing `BashTool(workdir, timeout)` callers keep local execution. SDK callers can pass the same policy through configuration:

```python
from pathlib import Path
from minibot import Minibot
from minibot.config import BashConfig, Config, ToolsConfig

bot = Minibot(config=Config(
    workdir=Path.cwd(),
    tools=ToolsConfig(bash=BashConfig(backend="docker")),
))
```

Use `image` to select another preloaded image that supplies `/bin/sh`. Install required programs while preparing the image. Minibot never pulls an image during execution and never falls back to host execution if Docker fails. Images that declare volumes are rejected. Each run pins the inspected image ID.

## Execution behavior

Each command gets a new container with the canonical workspace mounted read-write at `/workspace`. The shell starts in that directory. On POSIX, the container uses the host user's UID and GID. The root filesystem is read-only. A 64 MiB temporary filesystem is available at `/tmp`. The container has no network, no Linux capabilities, no privilege escalation, and configured memory, CPU, and PID limits.

Minibot does not forward host environment variables or mount your home directory or Docker socket into the container. Image-defined environment variables remain available. The Docker client uses its host environment to connect to a local Unix socket or Windows named pipe. Remote Docker endpoints are rejected. `DOCKER_CONTEXT` takes precedence over `DOCKER_HOST`.

Successful output keeps the existing text format. A failed command includes its exit code before output. Captured output uses bounded buffers and UTF-8 decoding with replacement for invalid bytes. Bash's dangerous-command checks and teammate workspace lock still apply.

`tools.timeout` covers Docker image inspection, container creation, execution, and result inspection. Timeout or cancellation triggers container removal and an absence check. Cleanup can extend wall time by bounded administrative calls of up to 10 seconds each. Container creation settles before removal. If a Docker administrative call times out, Minibot reports cleanup uncertainty with the unique container name. During cancellation, cleanup failures remain cancellation and also produce a diagnostic in logs and an Agent system event.

## Isolation limits

This policy isolates only the built-in Bash tool. File tools, Python tools, hooks, MCP tools, and custom SDK tools retain their existing access. Files and credentials already inside the workspace are available to the command and can be changed or deleted. The Docker daemon and selected image must be trusted. A container is not protection against a compromised daemon or kernel.

Local mode is host execution, not a sandbox. On POSIX, Minibot kills and reaps its shell process and stops ordinary children in the same process group on completion, timeout, or cancellation. A process that creates a separate session can escape that group. Windows uses best-effort process-tree termination through `taskkill /T /F`. Windows runtime behavior was not verified for this change.

## Run the verification

The normal suite uses real local subprocesses and deterministic Docker control barriers. Real Docker tests require a running local daemon and preloaded `debian:bookworm` image:

```sh
MINIBOT_TEST_DOCKER=1 uv run --with pytest --with pytest-asyncio pytest tests/test_bash_docker_integration.py -q
```

Opted-in Docker tests fail if Docker or the image is unavailable. They verify workspace access, exit status, fixed container policy, host environment and file isolation, timeout, cancellation, failed startup, and actual container absence. They use `debian:bookworm` regardless of your global configuration.
