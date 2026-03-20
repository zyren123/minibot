export type StreamEventType =
  | "assistant_start"
  | "assistant_reasoning_delta"
  | "assistant_delta"
  | "assistant_end"
  | "todo_snapshot"
  | "tool_call"
  | "tool_result"
  | "system";

export type TodoItemStatus = "pending" | "active" | "done";

export type TodoItem = {
  id: string;
  label: string;
  status: TodoItemStatus;
  detail?: string;
};

export type TodoSnapshot = {
  title: string;
  items: TodoItem[];
  completed: number;
  total: number;
  visible: boolean;
  completed_at?: string | null;
};

export type StreamEvent = {
  type?: StreamEventType;
  session_id?: string;
  message_id?: string;
  parent_user_message_id?: string | null;

  delta_text?: string;
  reasoning_text?: string;
  reasoning?: string;
  content?: string;
  finish_reason?: string;
  tool_calls?: Array<{ id: string; name: string; arguments: string }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  todo?: TodoSnapshot;

  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  tool_output?: string;
  is_error?: boolean;
  note?: string;

  message?: string;
  data?: {
    usage?: Usage;
    context_usage?: Usage;
    max_context_tokens?: number;
    auto_compact_threshold_tokens?: number;
    context_compacted?: boolean;
    total_tokens?: number;
    threshold?: number;
    error?: string;
    raw?: string;
    [key: string]: unknown;
  };
};

export type SessionMeta = {
  session_id: string;
  path: string;
  created_at: string;
  modified_at: string;
  message_count: number;
  preview: string;
};

export type Usage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
};

export type ReasoningEffort = "low" | "medium" | "high";

export type Message = {
  role: "user" | "assistant" | "tool";
  content: string;
  message_id?: string | null;
  tool_call_id?: string;
  tool_name?: string | null;
  is_error?: boolean | null;
  is_compaction?: boolean;
  parent_user_message_id?: string | null;
  reasoning?: string | null;
  usage?: Usage | null;
  context_usage?: Usage | null;
  tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }>;
};

export type SessionData = {
  session_id: string;
  messages: Message[];
};

export type DeletedMessageResult = {
  session_id: string;
  messages: Message[];
  deleted_message_ids: string[];
};

export type RegeneratedMessageResult = {
  session_id: string;
  messages: Message[];
  regenerated_from_message_id: string;
};

export type Config = {
  base_url: string | null;
  model: string | null;
  stream_enabled: boolean;
  skills_dirs: string[];
  user_skills_dir: string | null;
  project_skills_dir: string | null;
  default_skill_target: string;
  available_skill_targets: string[];
  tool_plugins: string[];
  api_key_masked: string | null;
};

export type BotMeta = {
  bot_id: string;
  name: string;
  is_default: boolean;
  enabled: boolean;
  subagent_exposable: boolean;
  subagent_name: string | null;
  subagent_description: string | null;
  attached_subagent_bot_ids: string[];
  chat_model_id: string | null;
  chat_ready: boolean;
  chat_disabled_reason: string | null;
};

export type BotConfig = {
  bot_id: string;
  name: string;
  enabled: boolean;
  base_url: string | null;
  model: string | null;
  chat_model_id: string | null;
  max_context_tokens: number;
  auto_compact_threshold_tokens: number;
  stream_enabled: boolean;
  api_key_masked: string | null;
  tool_plugins: string[];
  skills_disabled: string[];
  mcp_overrides: Record<string, boolean>;
  soul: string;
  subagent_exposable: boolean;
  subagent_name: string | null;
  subagent_description: string | null;
  attached_subagent_bot_ids: string[];
  chat_ready: boolean;
  chat_disabled_reason: string | null;
};

export type SkillInfo = {
  name: string;
  description: string;
  folder_name: string;
  source_type: string;
  scope: string;
  source_dir: string;
  resolved_path: string;
  resources: string[];
  writable: boolean;
  deletable: boolean;
  builtin: boolean;
  is_active: boolean;
  override_count: number;
  overridden_by_source_type: string | null;
  overridden_by_path: string | null;
};

export type SkillDeleteResult = {
  deleted: boolean;
  skill_name: string;
  scope: string;
  folder_name: string;
};

export type MCPServerInfo = {
  name: string;
  transport: string;
  enabled_default: boolean;
  command?: string | null;
  args: string[];
  url?: string | null;
  env_keys: string[];
};

export type ProviderRecord = {
  provider_id: string;
  name: string;
  base_url: string;
  kind: string;
  enabled: boolean;
  api_key_masked: string | null;
  created_at: string;
  updated_at: string;
};

export type RegisteredModel = {
  model_id: string;
  provider_id: string;
  model_name: string;
  label: string;
  enabled: boolean;
  added_via: string;
  created_at: string;
  updated_at: string;
};

export type DeletedModelsResult = {
  deleted_model_ids: string[];
  deleted_count: number;
};

export type AvailableModel = {
  model_id: string;
  provider_id: string;
  provider_name: string;
  model_name: string;
  label: string;
  base_url: string;
};

export type FetchedModel = {
  model_name: string;
  already_added: boolean;
};

export type DashboardData = {
  providers: ProviderRecord[];
  models: RegisteredModel[];
  bots: BotMeta[];
  available_models: AvailableModel[];
};

export type SubagentCandidate = {
  bot_id: string;
  name: string;
  subagent_name: string;
  subagent_description: string;
  enabled: boolean;
};
