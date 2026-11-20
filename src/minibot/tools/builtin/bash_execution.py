import asyncio
import csv
from dataclasses import dataclass
import io
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

from ...config.schema import BashConfig

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


async def _process(
    *args: str, cwd: Path, env: dict[str, str] | None = None, output_limit: int | None = None,
) -> ExecutionResult:
    per_stream_limit = _OUTPUT_LIMIT if output_limit is None else output_limit
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    ))
    proc = None
    readers = []
    try:
        proc = await asyncio.shield(spawn)
        readers = [
            asyncio.create_task(_drain(proc.stdout, per_stream_limit)),
            asyncio.create_task(_drain(proc.stderr, per_stream_limit)),
        ]
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
            await _kill_tree(proc)
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
class LocalBackend:
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


@dataclass(frozen=True)
class DockerBackend:
    workdir: Path
    config: BashConfig

    async def run(
        self, command: str, *, timeout: float, output_limit: int | None = None,
    ) -> ExecutionResult:
        name = f"minibot-bash-{uuid.uuid4().hex}"
        env = dict(os.environ)
        create_task = None
        may_exist = False

        async def cli(*args: str) -> ExecutionResult:
            async with asyncio.timeout(_ADMIN_TIMEOUT):
                return await _process("docker", *args, cwd=self.workdir, env=env)

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
                image = json.loads(await checked("image", "inspect", self.config.image))[0]
                if image.get("Config", {}).get("Volumes"):
                    raise RuntimeError("Docker Bash rejects image-declared volumes")
                mount = io.StringIO(newline="")
                csv.writer(mount, lineterminator="").writerow([
                    "type=bind", f"src={self.workdir.resolve()}", "dst=/workspace",
                ])
                args = [
                    "create", "--name", name, "--pull", "never", "--init",
                    "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--memory", f"{self.config.memory_mb}m",
                    "--cpus", str(self.config.cpus), "--pids-limit", str(self.config.pids_limit),
                    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m", "--mount", mount.getvalue(),
                    "--workdir", "/workspace",
                ]
                if os.name == "posix":
                    args += ["--user", f"{os.getuid()}:{os.getgid()}"]
                args += ["--entrypoint", "/bin/sh", image["Id"], "-c", command]
                may_exist = True
                create_task = asyncio.create_task(checked(*args))
                await asyncio.shield(create_task)
                result = await _process(
                    "docker", "start", "--attach", name, cwd=self.workdir, env=env, output_limit=output_limit,
                )
                state = json.loads(await checked("inspect", "--format", "{{json .State}}", name))
                if state["Running"] or state.get("Error"):
                    raise RuntimeError(f"Docker command did not finish: {state.get('Error') or 'still running'}")
                if result.exit_code != state["ExitCode"]:
                    raise RuntimeError(f"Docker attach failed: {result.stderr.strip()}")
                return result
        finally:
            interrupted = isinstance(sys.exception(), asyncio.CancelledError)
            if may_exist:
                try:
                    await _settle(asyncio.create_task(cleanup()))
                except (Exception, ExecutionCleanupCancelledError) as exc:
                    diagnostic = f"Docker cleanup uncertain for {name}: {exc}"
                    if interrupted or isinstance(exc, asyncio.CancelledError):
                        logging.getLogger(__name__).error(diagnostic)
                        raise ExecutionCleanupCancelledError(diagnostic) from exc
                    raise RuntimeError(diagnostic) from exc
