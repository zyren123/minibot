"""Stable identity for attaching a durable run to an execution workspace."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from ..config.schema import Config


@dataclass(frozen=True)
class WorkspaceIdentity:
    value: str

    @classmethod
    def from_root(
        cls, root: Path, backend: str = "local", *, profile: tuple[str, ...] = (),
    ) -> "WorkspaceIdentity":
        identity = json.dumps([1, str(root.resolve()), backend, profile], ensure_ascii=True)
        return cls(sha256(identity.encode("ascii")).hexdigest())


def legacy_workspace_identity(config: Config) -> WorkspaceIdentity:
    bash = config.tools.bash
    profile = ("bash", bash.backend)
    if bash.backend == "docker":
        profile += (bash.image, str(bash.memory_mb), str(float(bash.cpus)), str(bash.pids_limit))
    return WorkspaceIdentity.from_root(config.workdir, profile=profile)
