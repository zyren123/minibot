import asyncio
import csv
from contextlib import suppress
from dataclasses import dataclass, field
import io
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir
import uuid

from ...config.schema import BashConfig, WorkspaceConfig
from ...config.workspace import require_outside_root
from ...core.workspace import restricted_root_key

_OUTPUT_LIMIT = 50000
_ADMIN_TIMEOUT = 10.0


class ExecutionCleanupCancelledError(asyncio.CancelledError):
    """Cancellation completed with an explicit resource-cleanup diagnostic."""


@dataclass(frozen=True)
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    output_exceeded: bool = False
    incomplete_output: bool = False


@dataclass(frozen=True)
class _DrainedOutput:
    data: bytes
    exceeded: bool
    incomplete: bool


async def _settle(task: asyncio.Task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    try:
        result = task.result()
    except Exception as exc:
        if cancelled:
            raise ExecutionCleanupCancelledError(str(exc)) from exc
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _drain(reader: asyncio.StreamReader, limit: int) -> _DrainedOutput:
    output = bytearray()
    exceeded = False
    try:
        while chunk := await reader.read(65536):
            spare = limit - len(output)
            if len(chunk) > spare:
                exceeded = True
            output.extend(chunk[:spare])
    except asyncio.CancelledError:
        # Only the owning supervisor cancels drainers after its bounded drain.
        return _DrainedOutput(bytes(output), exceeded, True)
    return _DrainedOutput(bytes(output), exceeded, False)


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.returncode is None:
        killer = None
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(_ADMIN_TIMEOUT):
                _, error = await killer.communicate()
            if killer.returncode and proc.returncode is None:
                raise RuntimeError(error.decode("utf-8", "replace"))
        except (OSError, TimeoutError, RuntimeError) as exc:
            if killer is not None and killer.returncode is None:
                killer.kill()
                await killer.wait()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise RuntimeError(f"taskkill could not confirm tree cleanup: {exc}") from exc
    while proc.returncode is None:
        await asyncio.sleep(0.01)
    await proc.wait()


async def _feed_stdin(writer: asyncio.StreamWriter, data: bytes) -> None:
    try:
        with suppress(BrokenPipeError, ConnectionResetError):
            for start in range(0, len(data), 65536):
                writer.write(data[start:start + 65536])
                await writer.drain()
    finally:
        writer.close()


async def _process(
    *args: str, cwd: Path, env: dict[str, str] | None = None, output_limit: int | None = None,
    stdin: bytes | None = None,
) -> ExecutionResult:
    per_stream_limit = _OUTPUT_LIMIT if output_limit is None else output_limit
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    ))
    proc = None
    readers = []
    writer = None
    try:
        proc = await asyncio.shield(spawn)
        readers = [
            asyncio.create_task(_drain(proc.stdout, per_stream_limit)),
            asyncio.create_task(_drain(proc.stderr, per_stream_limit)),
        ]
        if stdin is not None:
            writer = asyncio.create_task(_feed_stdin(proc.stdin, stdin))
        # Process.wait can wait for inherited pipes after the shell has exited.
        while proc.returncode is None:
            await asyncio.sleep(0.01)
    finally:
        interrupted = isinstance(sys.exception(), asyncio.CancelledError)

        async def finalize():
            nonlocal proc, readers
            if proc is None:
                proc = await spawn
            if not readers:
                readers = [
                    asyncio.create_task(_drain(proc.stdout, per_stream_limit)),
                    asyncio.create_task(_drain(proc.stderr, per_stream_limit)),
                ]
            if writer is not None:
                writer.cancel()
            if proc.stdin is not None:
                proc.stdin.close()
            await _kill_tree(proc)
            if writer is not None:
                await asyncio.gather(writer, return_exceptions=True)
            # Detached sessions are outside local ownership and may retain pipes.
            done, pending = await asyncio.wait(readers, timeout=1)
            for reader in pending:
                reader.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            # asyncio Process has no public pipe-close method. Escaped sessions may
            # retain descriptors, so close our transports after the bounded drain.
            proc._transport.close()
        try:
            await _settle(asyncio.create_task(finalize()))
        except Exception as exc:
            if interrupted:
                diagnostic = f"Process cleanup failed during cancellation: {exc}"
                logging.getLogger(__name__).error(diagnostic)
                raise ExecutionCleanupCancelledError(diagnostic) from exc
            raise
    stdout, stderr = [
        reader.result() if not reader.cancelled() else _DrainedOutput(b"", False, True)
        for reader in readers
    ]
    exceeded = output_limit is not None and (
        stdout.exceeded or stderr.exceeded or len(stdout.data) + len(stderr.data) > output_limit
    )
    return ExecutionResult(
        stdout.data.decode("utf-8", "replace"), stderr.data.decode("utf-8", "replace"), proc.returncode,
        output_exceeded=exceeded, incomplete_output=stdout.incomplete or stderr.incomplete,
    )


@dataclass(frozen=True)
class LocalCommand:
    workdir: Path

    async def run(
        self, command: str, *, timeout: float, output_limit: int | None = None,
    ) -> ExecutionResult:
        async with asyncio.timeout(timeout):
            if os.name == "nt":
                return await _process(
                    os.environ.get("COMSPEC", "cmd.exe"), "/c", command,
                    cwd=self.workdir, output_limit=output_limit,
                )
            return await _process("/bin/sh", "-c", command, cwd=self.workdir, output_limit=output_limit)


def _trusted_docker_cli(workdir: Path, env: dict[str, str]) -> str:
    search_dirs = []
    for entry in env.get("PATH", os.defpath).split(os.pathsep):
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        try:
            require_outside_root(workdir, directory, "Docker CLI search directory")
        except ValueError:
            continue
        search_dirs.append(str(directory))
    env["PATH"] = os.pathsep.join(search_dirs)
    executable = shutil.which("docker", path=env["PATH"]) if search_dirs else None
    if executable is None:
        raise RuntimeError("Docker CLI was not found on a trusted absolute PATH")
    executable_path = Path(executable)
    require_outside_root(workdir, executable_path, "Docker CLI")
    config_dir = Path(env.get("DOCKER_CONFIG") or Path.home() / ".docker").expanduser().absolute()
    require_outside_root(workdir, config_dir, "Docker CLI configuration")
    env["DOCKER_CONFIG"] = str(config_dir)
    return str(executable_path)


@dataclass(frozen=True)
class DockerCommand:
    workdir: Path
    config: BashConfig | WorkspaceConfig
    _root_key: tuple[int, int] | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.config, WorkspaceConfig):
            object.__setattr__(self, "_root_key", restricted_root_key(self.workdir))

    def bind_root(self) -> Path:
        if self._root_key is None:
            return self.workdir.resolve()
        if restricted_root_key(self.workdir) != self._root_key:
            raise ValueError("The restricted Docker workspace directory was replaced.")
        return self.workdir

    async def run(
        self, command: str, *, timeout: float, output_limit: int | None = None,
    ) -> ExecutionResult:
        return await self.execute("/bin/sh", "-c", command, timeout=timeout, output_limit=output_limit)

    async def execute(
        self, program: str, *arguments: str, timeout: float, output_limit: int | None = None,
        stdin: bytes | None = None,
    ) -> ExecutionResult:
        restricted = isinstance(self.config, WorkspaceConfig)
        label = "workspace" if restricted else "bash"
        name = f"minibot-{label}-{uuid.uuid4().hex}"
        cli_config = None
        env = dict(os.environ)
        create_task = None
        may_exist = False
        executable = _trusted_docker_cli(self.bind_root(), env) if restricted else "docker"
        client_cwd = Path(self.workdir.anchor) if restricted else self.workdir

        async def cli(*args: str) -> ExecutionResult:
            async with asyncio.timeout(_ADMIN_TIMEOUT):
                return await _process(executable, *args, cwd=client_cwd, env=env)

        async def checked(*args: str) -> str:
            result = await cli(*args)
            if result.exit_code:
                raise RuntimeError(f"Docker {' '.join(args[:2])} failed: {result.stderr.strip() or result.stdout.strip()}")
            return result.stdout

        async def cleanup():
            create_uncertain = False
            create_failed = False
            if create_task is not None:
                try:
                    await create_task
                except TimeoutError:
                    create_uncertain = True
                except Exception:
                    create_failed = True
            removal = await cli("rm", "-f", "--volumes", name)
            remaining = await checked("ps", "--all", "--filter", f"name=^{name}$", "--format", "{{.ID}}")
            if create_uncertain:
                raise RuntimeError("create exceeded its administrative deadline; daemon completion cannot be proved")
            if remaining.strip() or (removal.exit_code and not create_failed):
                raise RuntimeError(f"Docker removal failed: {removal.stderr.strip()}")

        try:
            async with asyncio.timeout(timeout):
                # DOCKER_CONTEXT takes precedence over DOCKER_HOST in the Docker CLI.
                endpoint = env.get("DOCKER_HOST") if not env.get("DOCKER_CONTEXT") else None
                if not endpoint:
                    context = env.get("DOCKER_CONTEXT") or (await checked("context", "show")).strip()
                    contexts = json.loads(await checked("context", "inspect", context))
                    endpoint = contexts[0]["Endpoints"]["docker"]["Host"]
                if not endpoint.startswith(("unix://", "npipe://")):
                    raise RuntimeError("Docker Bash requires a local Unix socket or Windows named pipe endpoint")
                env.pop("DOCKER_CONTEXT", None)
                env["DOCKER_HOST"] = endpoint
                if restricted:
                    # Docker CLI config can automatically add proxy environment variables.
                    temp_parent = Path(gettempdir())
                    require_outside_root(self.workdir, temp_parent, "Docker CLI temporary directory")
                    cli_config = TemporaryDirectory(prefix="minibot-docker-", dir=temp_parent)
                    env["DOCKER_CONFIG"] = cli_config.name
                image = json.loads(await checked("image", "inspect", self.config.image))[0]
                if image.get("Config", {}).get("Volumes"):
                    raise RuntimeError("Docker Bash rejects image-declared volumes")
                mount = io.StringIO(newline="")
                mount_options = ["type=bind", f"src={self.bind_root()}", "dst=/workspace"]
                if restricted:
                    mount_options.append("bind-recursive=disabled")
                csv.writer(mount, lineterminator="").writerow(mount_options)
                args = [
                    "create", "--name", name, "--pull", "never", "--init",
                    "--network", self.config.network if restricted else "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--memory", f"{self.config.memory_mb}m",
                    "--cpus", str(self.config.cpus), "--pids-limit", str(self.config.pids_limit),
                    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m", "--mount", mount.getvalue(),
                    "--workdir", "/workspace",
                ]
                if os.name == "posix":
                    args += ["--user", f"{os.getuid()}:{os.getgid()}"]
                if stdin is not None:
                    args.append("--interactive")
                args += ["--entrypoint", program, image["Id"], *arguments]
                may_exist = True
                create_task = asyncio.create_task(checked(*args))
                await asyncio.shield(create_task)
                if restricted:
                    self.bind_root()
                attach = [executable, "start", "--attach"]
                input_options = {}
                if stdin is not None:
                    attach.append("--interactive")
                    input_options["stdin"] = stdin
                result = await _process(
                    *attach, name, cwd=client_cwd, env=env, output_limit=output_limit, **input_options,
                )
                state = json.loads(await checked("inspect", "--format", "{{json .State}}", name))
                if state["Running"] or state.get("Error"):
                    raise RuntimeError(f"Docker command did not finish: {state.get('Error') or 'still running'}")
                if result.exit_code != state["ExitCode"]:
                    raise RuntimeError(f"Docker attach failed: {result.stderr.strip()}")
                return result
        finally:
            interrupted = isinstance(sys.exception(), asyncio.CancelledError)
            try:
                if may_exist:
                    try:
                        await _settle(asyncio.create_task(cleanup()))
                    except (Exception, ExecutionCleanupCancelledError) as exc:
                        diagnostic = f"Docker cleanup uncertain for {name}: {exc}"
                        if interrupted or isinstance(exc, asyncio.CancelledError):
                            logging.getLogger(__name__).error(diagnostic)
                            raise ExecutionCleanupCancelledError(diagnostic) from exc
                        raise RuntimeError(diagnostic) from exc
            finally:
                if cli_config is not None:
                    cli_config.cleanup()
