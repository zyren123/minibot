"""HTTP API models for the Minibot server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, description="Session id to use (optional).")
    prompt: str = Field(min_length=1)
    reasoning_effort: Literal["low", "medium", "high"] | None = None


class UsageResponse(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class MessageResponse(BaseModel):
    message_id: str | None = None
    role: str
    content: str = ""
    tool_call_id: str | None = None
    is_compaction: bool | None = None
    parent_user_message_id: str | None = None
    reasoning: str | None = None
    usage: UsageResponse | None = None
    context_usage: UsageResponse | None = None
    tool_calls: list[dict[str, object]] = Field(default_factory=list)


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[MessageResponse] = Field(default_factory=list)


class MessageDeleteResponse(BaseModel):
    session_id: str
    messages: list[MessageResponse] = Field(default_factory=list)
    deleted_message_ids: list[str] = Field(default_factory=list)


class MessageRegenerateResponse(BaseModel):
    session_id: str
    messages: list[MessageResponse] = Field(default_factory=list)
    regenerated_from_message_id: str


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
    enabled: bool = True
    subagent_exposable: bool = False
    subagent_name: str | None = None
    subagent_description: str | None = None
    attached_subagent_bot_ids: list[str] = Field(default_factory=list)
    chat_model_id: str | None = None
    chat_ready: bool = True
    chat_disabled_reason: str | None = None


class BotConfigResponse(BaseModel):
    bot_id: str
    name: str
    enabled: bool = True
    base_url: str | None = None
    model: str | None = None
    chat_model_id: str | None = None
    max_context_tokens: int = 0
    auto_compact_threshold_tokens: int = 0
    stream_enabled: bool = True
    api_key_masked: str | None = None
    tool_plugins: list[str] = Field(default_factory=list)
    skills_disabled: list[str] = Field(default_factory=list)
    mcp_overrides: dict[str, bool] = Field(default_factory=dict)
    soul: str = ""
    subagent_exposable: bool = False
    subagent_name: str | None = None
    subagent_description: str | None = None
    attached_subagent_bot_ids: list[str] = Field(default_factory=list)
    chat_ready: bool = True
    chat_disabled_reason: str | None = None


class BotConfigUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    base_url: str | None = None
    model: str | None = None
    chat_model_id: str | None = None
    api_key: str | None = None
    stream_enabled: bool | None = None
    tool_plugins: list[str] | None = None
    skills_disabled: list[str] | None = None
    mcp_overrides: dict[str, bool | None] | None = None
    soul: str | None = None
    subagent_exposable: bool | None = None
    subagent_name: str | None = None
    subagent_description: str | None = None
    attached_subagent_bot_ids: list[str] | None = None


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


class ProviderResponse(BaseModel):
    provider_id: str
    name: str
    base_url: str
    kind: str = "openai_compatible"
    enabled: bool = True
    api_key_masked: str | None = None
    created_at: str = ""
    updated_at: str = ""


class ProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    enabled: bool = True


class ProviderUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


class ModelResponse(BaseModel):
    model_id: str
    provider_id: str
    model_name: str
    label: str
    enabled: bool = True
    added_via: str = "manual"
    created_at: str = ""
    updated_at: str = ""


class ModelCreateRequest(BaseModel):
    model_names: list[str] = Field(default_factory=list)
    added_via: str = "manual"


class ModelDeleteRequest(BaseModel):
    model_ids: list[str] = Field(default_factory=list)


class ModelUpdateRequest(BaseModel):
    label: str | None = None
    enabled: bool | None = None


class ModelDeleteResponse(BaseModel):
    deleted_model_ids: list[str] = Field(default_factory=list)
    deleted_count: int = 0


class AvailableModelResponse(BaseModel):
    model_id: str
    provider_id: str
    provider_name: str
    model_name: str
    label: str
    base_url: str


class FetchedModelResponse(BaseModel):
    model_name: str
    already_added: bool = False


class DashboardResponse(BaseModel):
    providers: list[ProviderResponse] = Field(default_factory=list)
    models: list[ModelResponse] = Field(default_factory=list)
    bots: list[BotMetaResponse] = Field(default_factory=list)
    available_models: list[AvailableModelResponse] = Field(default_factory=list)


class SubagentCandidateResponse(BaseModel):
    bot_id: str
    name: str
    subagent_name: str
    subagent_description: str
    enabled: bool = True
