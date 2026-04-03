export type StreamEventType =
  | "assistant_start"
  | "assistant_reasoning_delta"
  | "assistant_delta"
  | "assistant_end"
  | "ask_user_question"
  | "ask_user_answer_received"
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
  context_usage?: Usage;
  context_compacted?: boolean;
  todo?: TodoSnapshot;
  question_id?: string;
  prompt?: string;
  options?: Array<{ label: string; value: string }>;
  allow_free_text?: boolean;
  required?: boolean;
  answer_text?: string;
  selected_option_value?: string | null;

  tool_call_id?: string;
  tool_name?: string | null;
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
  tool_args?: Record<string, unknown> | null;
  is_error?: boolean | null;
  is_compaction?: boolean;
  parent_user_message_id?: string | null;
  completion_state?: "complete" | "interrupted" | null;
  reasoning?: string | null;
  usage?: Usage | null;
  context_usage?: Usage | null;
  tool_calls?: Array<
    | { id: string; name: string; arguments: string }
    | { id: string; type: string; function: { name: string; arguments: string } }
  >;
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

export type PendingQuestion = {
  question_id: string;
  message_id?: string | null;
  prompt: string;
  options: Array<{ label: string; value: string }>;
  allow_free_text: boolean;
  required: boolean;
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
  memory_backend: "sqlite" | "postgres";
  memory_database_url_configured: boolean;
  memory_database_url_value: string | null;
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
  teams_enabled: boolean;
  api_key_masked: string | null;
  tool_plugins: string[];
  skills_disabled: string[];
  mcp_overrides: Record<string, boolean>;
  active_memory_namespace?: string | null;
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

export type MemoryNamespace = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
};

export type MemoryTreeNode = {
  uri: string;
  title: string;
  kind: "folder" | "memory";
  node_type: string | null;
  children: MemoryTreeNode[];
};

export type MemoryTreeResponse = {
  nodes: MemoryTreeNode[];
};

export type MemoryGraphNode = {
  uri: string;
  title: string;
  kind: "folder" | "memory";
  node_type: string | null;
};

export type MemoryGraphEdge = {
  source: string;
  target: string;
};

export type MemoryGraphResponse = {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
};

export type MemoryNodeChild = {
  uri: string;
  title: string;
  kind: "folder" | "memory";
  node_type: string | null;
};

export type MemoryNodeDetail = {
  id: string;
  uri: string;
  title: string;
  kind: "folder" | "memory";
  node_type: string | null;
  is_core: boolean;
  priority: number;
  content: string;
  triggers: string[];
  children: MemoryNodeChild[];
};

export type MemorySearchResult = {
  uri: string;
  title: string;
  kind: "folder" | "memory";
  node_type: string | null;
  matched_by: string;
  snippet: string;
  parent_uri: string | null;
};

export type MemorySystemViewName = "boot" | "index" | "glossary";

export type MemorySystemView = {
  uri: `system://${MemorySystemViewName}`;
  content: string;
};

export type MCPServerInfo = {
  name: string;
  transport: string;
  enabled_default: boolean;
  command?: string | null;
  args: string[];
  url?: string | null;
  env_keys: string[];
  connected: boolean;
};

export type UpdateMCPServerRequest = {
  enabled_default?: boolean;
  command?: string | null;
  args?: string[];
  url?: string | null;
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
  platforms: PlatformConnection[];
  available_models: AvailableModel[];
};

export type SubagentCandidate = {
  bot_id: string;
  name: string;
  subagent_name: string;
  subagent_description: string;
  enabled: boolean;
};

export type PlatformConnection = {
  platform_id: string;
  name: string;
  kind: "feishu" | "telegram" | "whatsapp";
  enabled: boolean;
  bound_bot_id: string;
  bound_bot_name: string;
  mode: "websocket";
  scope: "private";
  app_id: string;
  app_secret_masked: string | null;
  connected: boolean;
  last_error: string | null;
  last_event_at: string | null;
  created_at: string;
  updated_at: string;
};
