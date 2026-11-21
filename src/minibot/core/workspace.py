"""Stable identity for attaching a durable run to an execution workspace."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import stat

from ..config.schema import BashConfig, Config


@dataclass(frozen=True)
class WorkspaceIdentity:
    value: str

    @classmethod
    def from_root(
        cls, root: Path, backend: str = "local", *, profile: tuple[str, ...] = (),
    ) -> "WorkspaceIdentity":
        identity = json.dumps([1, str(root.resolve()), backend, profile], ensure_ascii=True)
        return cls(sha256(identity.encode("ascii")).hexdigest())


@dataclass(frozen=True)
class DockerContainer:
    name: str
    container_id: str | None


def legacy_workspace_identity(config: Config) -> WorkspaceIdentity:
    return trusted_workspace_identity(config.workdir, config.tools.bash)


def trusted_workspace_identity(root: Path, bash: BashConfig) -> WorkspaceIdentity:
    profile = ("bash", bash.backend)
    if bash.backend == "docker":
        profile += (bash.image, str(bash.memory_mb), str(float(bash.cpus)), str(bash.pids_limit))
    return WorkspaceIdentity.from_root(root, profile=profile)


def restricted_root_key(root: Path) -> tuple[int, int]:
    try:
        current = root.lstat()
    except OSError as exc:
        raise ValueError("The restricted Docker workspace directory is unavailable.") from exc
    if not stat.S_ISDIR(current.st_mode) or root.resolve() != root:
        raise ValueError("The restricted Docker workspace must resolve to its original directory.")
    return current.st_dev, current.st_ino


def configured_workspace_identity(config: Config) -> WorkspaceIdentity:
    policy = config.workspace
    if not policy.restricted:
        return legacy_workspace_identity(config)
    root = config.workdir.resolve()
    device, inode = restricted_root_key(root)
    return WorkspaceIdentity.from_root(
        root, "docker",
        profile=(
            "workspace-v1", str(device), str(inode), policy.image, policy.network,
            str(policy.memory_mb), str(float(policy.cpus)),
            str(policy.pids_limit), str(float(policy.timeout or config.tools.timeout)),
            "bash-disabled" if config.tools.bash.backend == "disabled" else "bash-enabled",
        ),
    )
