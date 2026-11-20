"""Configuration schema definitions."""

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Literal


@dataclass
class LLMConfig:
    """LLM client configuration."""
    base_url: str | None = None
    api_key: str | None = None
    model: str = "gpt-4.1-mini"
    max_tokens: int = 8000
    max_context_tokens: int = 100000
    stream_enabled: bool = True


@dataclass(frozen=True)
class BashConfig:
    """Bash execution policy. Local execution remains the default."""
    backend: Literal["disabled", "local", "docker"] = "local"
    image: str = "debian:bookworm"
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 128

    def __post_init__(self):
        if self.backend not in ("disabled", "local", "docker"):
            raise ValueError("tools.bash.backend must be disabled, local, or docker")
        if not isinstance(self.image, str) or not self.image.strip() or self.image.startswith("-"):
            raise ValueError("tools.bash.image must be a nonempty image reference")
        for name in ("memory_mb", "pids_limit"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"tools.bash.{name} must be a positive integer")
        if type(self.cpus) not in (int, float) or not math.isfinite(self.cpus) or self.cpus <= 0:
            raise ValueError("tools.bash.cpus must be a positive finite number")


@dataclass
class ToolsConfig:
    """Tools configuration."""
    enabled: list[str] = field(default_factory=lambda: ["*"])
    disabled: list[str] = field(default_factory=list)
    timeout: int | float = 60
    bash: BashConfig = field(default_factory=BashConfig)

    def __post_init__(self):
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("tools.timeout must be a positive finite number")
        if not isinstance(self.bash, BashConfig):
            raise ValueError("tools.bash must be a BashConfig")


@dataclass
class HookDefinition:
    """Single hook definition."""
    event: str
    handler: str  # Script path or module:function
    timeout: int = 30
    enabled: bool = True


@dataclass
class HooksConfig:
    """Hooks system configuration."""
    enabled: bool = True
    hooks_dir: Path = field(default_factory=lambda: Path("hooks"))
    hooks: list[HookDefinition] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    """Single MCP server configuration."""
    name: str
    transport: str = "stdio"  # stdio or sse
    command: str | None = None  # For stdio
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # For sse
    enabled: bool = True


@dataclass
class MemoryConfig:
    """Memory system configuration."""
    enabled: bool = True
    memory_dir: str = "memory"
    backend: str = "sqlite"
    database_url: str | None = None
    active_namespace: str | None = None
    long_term_max_lines: int = 200
    daily_lookback_days: int = 1


@dataclass
class MCPConfig:
    """MCP client configuration."""
    enabled: bool = True
    servers: list[MCPServerConfig] = field(default_factory=list)


@dataclass
class TeamsConfig:
    """Agent teams configuration."""

    enabled: bool = True
    max_members: int = 6
    default_members: int = 3
    log_dir: str = ".minibot/teams"
    wait_timeout_sec: int = 30
    quiet_teammates: bool = True
    debug_teammate_output: bool = False


@dataclass
class SubagentConfig:
    """Single subagent type override."""
    description: str | None = None
    tools: list[str] | None = None
    prompt: str | None = None
    skills_enabled: bool = False
    enabled: bool = True


@dataclass
class SubagentsConfig:
    """Subagents configuration."""
    agents: dict[str, SubagentConfig] = field(default_factory=dict)


@dataclass
class SessionConfig:
    """Session persistence configuration."""
    enabled: bool = True
    sessions_dir: str = "sessions"  # relative to app_home


@dataclass
class Config:
    """Main configuration."""
    workdir: Path = field(default_factory=Path.cwd)
    app_home: Path | None = None
    project_root: Path | None = None
    skills_dir: Path = field(default_factory=lambda: Path("skills"))
    llm: LLMConfig = field(default_factory=LLMConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    teams: TeamsConfig = field(default_factory=TeamsConfig)
    subagents: SubagentsConfig = field(default_factory=SubagentsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    trace_dir: str | Path | None = None

    def __post_init__(self):
        if isinstance(self.workdir, str):
            self.workdir = Path(self.workdir)
        if isinstance(self.app_home, str):
            self.app_home = Path(self.app_home)
        if isinstance(self.project_root, str):
            self.project_root = Path(self.project_root)
        if isinstance(self.skills_dir, str):
            self.skills_dir = Path(self.skills_dir)
        if self.project_root is None:
            self.project_root = self.workdir
        if self.app_home is None:
            self.app_home = self.workdir / ".minibot"
        if self.trace_dir is not None:
            trace_path = Path(self.trace_dir).expanduser()
            self.trace_dir = trace_path if trace_path.is_absolute() else self.app_home / trace_path
