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
    max_tool_output_chars: int = 16000

    def __post_init__(self):
        if type(self.max_tool_output_chars) is not int or self.max_tool_output_chars < 32:
            raise ValueError("llm.max_tool_output_chars must be an integer of at least 32")


@dataclass(frozen=True)
class RuntimeBudgetConfig:
    """Cumulative limits for one active execution segment."""

    max_llm_calls: int = 30
    max_tool_calls: int = 100
    max_wall_time: int | float = 600
    max_input_tokens: int = 200_000
    max_output_tokens: int = 50_000

    def __post_init__(self):
        for name in ("max_llm_calls", "max_tool_calls", "max_input_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"runtime_budget.{name} must be a positive integer")
        limit = self.max_wall_time
        if type(limit) not in (int, float) or not 0 < limit < math.inf:
            raise ValueError("runtime_budget.max_wall_time must be a positive finite number")


ToolPermission = Literal["allow", "deny", "ask"]


@dataclass(frozen=True)
class ToolPermissionsConfig:
    """Execution policy for model-requested tools."""

    mode: Literal["off", "manual", "auto_review"] = "off"
    default: ToolPermission = "ask"
    rules: dict[str, ToolPermission] = field(default_factory=dict)
    auto_review_paths: tuple[str, ...] = ()
    timeout: int | float = 120

    def __post_init__(self):
        choices = ("allow", "deny", "ask")
        if self.mode not in ("off", "manual", "auto_review"):
            raise ValueError("tool_permissions.mode must be off, manual, or auto_review")
        if self.default not in choices:
            raise ValueError("tool_permissions.default must be allow, deny, or ask")
        if not isinstance(self.rules, dict) or any(
            not isinstance(name, str) or not name.strip() or decision not in choices
            for name, decision in self.rules.items()
        ):
            raise ValueError("tool_permissions.rules must map tool names to allow, deny, or ask")
        paths = self.auto_review_paths
        if not isinstance(paths, (tuple, list)) or any(
            not isinstance(path, str) or not path.strip() or "\\" in path
            or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/"))
            for path in paths
        ):
            raise ValueError("tool_permissions.auto_review_paths must contain workspace-relative glob patterns")
        if type(self.timeout) not in (int, float) or not 0 < self.timeout <= 3600:
            raise ValueError("tool_permissions.timeout must be in (0, 3600] seconds")
        object.__setattr__(self, "rules", dict(self.rules))
        object.__setattr__(self, "auto_review_paths", tuple(paths))


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
    transport: str = "stdio"  # stdio, sse, or streamable_http
    command: str | None = None  # For stdio
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # For sse and streamable_http
    enabled: bool = True
    headers: dict[str, str] = field(default_factory=dict, repr=False)


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
    runtime_budget: RuntimeBudgetConfig = field(default_factory=RuntimeBudgetConfig)
    tool_permissions: ToolPermissionsConfig = field(default_factory=ToolPermissionsConfig)

    def __post_init__(self):
        if not isinstance(self.tool_permissions, ToolPermissionsConfig):
            raise ValueError("tool_permissions must be a ToolPermissionsConfig")
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
