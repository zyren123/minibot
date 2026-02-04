"""Configuration schema definitions."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMConfig:
    """LLM client configuration."""
    base_url: str | None = None
    api_key: str | None = None
    model: str = "gpt-4.1-mini"
    max_tokens: int = 8000


@dataclass
class ToolsConfig:
    """Tools configuration."""
    enabled: list[str] = field(default_factory=lambda: ["*"])
    disabled: list[str] = field(default_factory=list)
    timeout: int = 60


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
class MCPConfig:
    """MCP client configuration."""
    enabled: bool = True
    servers: list[MCPServerConfig] = field(default_factory=list)


@dataclass
class Config:
    """Main configuration."""
    workdir: Path = field(default_factory=Path.cwd)
    skills_dir: Path = field(default_factory=lambda: Path("skills"))
    llm: LLMConfig = field(default_factory=LLMConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)

    def __post_init__(self):
        if isinstance(self.workdir, str):
            self.workdir = Path(self.workdir)
        if isinstance(self.skills_dir, str):
            self.skills_dir = Path(self.skills_dir)
