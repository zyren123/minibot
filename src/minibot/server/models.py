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

