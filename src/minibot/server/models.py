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
    completion_state: str | None = None
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


class MessageRegenerateRequest(BaseModel):
    reasoning_effort: Literal["low", "medium", "high"] | None = None


class PendingQuestionResponse(BaseModel):
    question_id: str
    message_id: str | None = None
    prompt: str
    options: list[dict[str, str]] = Field(default_factory=list)
    allow_free_text: bool = True
    required: bool = True


class SubmitUserAnswerRequest(BaseModel):
    question_id: str
    answer_text: str = ""
    selected_option_value: str | None = None


class ConfigResponse(BaseModel):
    base_url: str | None = None
    model: str | None = None
    stream_enabled: bool = True
    skills_dirs: list[str] = Field(default_factory=list)
    user_skills_dir: str | None = None
    project_skills_dir: str | None = None
    default_skill_target: str = "user"
    available_skill_targets: list[str] = Field(default_factory=list)
    tool_plugins: list[str] = Field(default_factory=list)
    api_key_masked: str | None = None
    memory_backend: str = "sqlite"
    memory_database_url_configured: bool = False
    memory_database_url_value: str | None = None


class ConfigUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    stream_enabled: bool | None = None
    skills_dirs: list[str] | None = None
    tool_plugins: list[str] | None = None
    memory_backend: str | None = None
    memory_database_url_value: str | None = None


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
    teams_enabled: bool = True
    api_key_masked: str | None = None
    tool_plugins: list[str] = Field(default_factory=list)
    skills_disabled: list[str] = Field(default_factory=list)
    mcp_overrides: dict[str, bool] = Field(default_factory=dict)
    active_memory_namespace: str | None = None
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
    teams_enabled: bool | None = None
    tool_plugins: list[str] | None = None
    skills_disabled: list[str] | None = None
    mcp_overrides: dict[str, bool | None] | None = None
    active_memory_namespace: str | None = None
    soul: str | None = None
    subagent_exposable: bool | None = None
    subagent_name: str | None = None
    subagent_description: str | None = None
    attached_subagent_bot_ids: list[str] | None = None


class SkillInfo(BaseModel):
    name: str
    description: str
    folder_name: str
    source_type: str
    scope: str
    source_dir: str
    resolved_path: str
    resources: list[str] = Field(default_factory=list)
    writable: bool = False
    deletable: bool = False
    builtin: bool = False
    is_active: bool = True
    override_count: int = 0
    overridden_by_source_type: str | None = None
    overridden_by_path: str | None = None


class MemoryNamespaceCreateRequest(BaseModel):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None


class MemoryNodeCreateRequest(BaseModel):
    parent_uri: str | None = None
    slug: str | None = None
    title: str = Field(min_length=1)
    kind: Literal["folder", "memory"]
    node_type: str | None = None
    content: str = ""
    is_core: bool = False
    priority: int = 0


class SkillCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    scope: Literal["user", "project"] = "user"


class SkillDeleteResponse(BaseModel):
    deleted: bool = False
    skill_name: str
    scope: str
    folder_name: str


class MCPServerInfo(BaseModel):
    name: str
    transport: str
    enabled_default: bool = True
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    connected: bool = False


class MCPServerUpdateRequest(BaseModel):
    enabled_default: bool | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None


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


PlatformKind = Literal["feishu", "telegram", "whatsapp"]


class PlatformConnectionResponse(BaseModel):
    platform_id: str
    name: str
    kind: PlatformKind = "feishu"
    enabled: bool = True
    bound_bot_id: str
    bound_bot_name: str
    mode: Literal["websocket"] = "websocket"
    scope: Literal["private"] = "private"
    app_id: str = ""
    app_secret_masked: str | None = None
    connected: bool = False
    last_error: str | None = None
    last_event_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class PlatformConnectionCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    kind: PlatformKind = "feishu"
    enabled: bool = True
    bound_bot_id: str = "default"
    app_id: str | None = None
    app_secret: str | None = None


class PlatformConnectionUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    bound_bot_id: str | None = None
    app_id: str | None = None
    app_secret: str | None = None


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
    platforms: list[PlatformConnectionResponse] = Field(default_factory=list)
    available_models: list[AvailableModelResponse] = Field(default_factory=list)


class SubagentCandidateResponse(BaseModel):
    bot_id: str
    name: str
    subagent_name: str
    subagent_description: str
    enabled: bool = True
