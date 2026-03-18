"""HTTP API models for the Minibot server."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="Session id to use (optional).")
    prompt: str = Field(min_length=1)


class ConfigResponse(BaseModel):
    base_url: str | None = None
    model: str | None = None
    stream_enabled: bool = True
    skills_dirs: list[str] = Field(default_factory=list)
    tool_plugins: list[str] = Field(default_factory=list)
    api_key_masked: str | None = None


class ConfigUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    stream_enabled: bool | None = None
    skills_dirs: list[str] | None = None
    tool_plugins: list[str] | None = None


class BotCreateRequest(BaseModel):
    name: str | None = None


class BotMetaResponse(BaseModel):
    bot_id: str
    name: str
    is_default: bool = False


class BotConfigResponse(BaseModel):
    bot_id: str
    name: str
    base_url: str | None = None
    model: str | None = None
    stream_enabled: bool = True
    api_key_masked: str | None = None
    tool_plugins: list[str] = Field(default_factory=list)
    skills_disabled: list[str] = Field(default_factory=list)
    mcp_overrides: dict[str, bool] = Field(default_factory=dict)
    soul: str = ""


class BotConfigUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    stream_enabled: bool | None = None
    tool_plugins: list[str] | None = None
    skills_disabled: list[str] | None = None
    mcp_overrides: dict[str, bool | None] | None = None
    soul: str | None = None


class SkillInfo(BaseModel):
    name: str
    description: str


class MCPServerInfo(BaseModel):
    name: str
    transport: str
    enabled_default: bool = True
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_keys: list[str] = Field(default_factory=list)
