export type StreamEventType =
  | "assistant_start"
  | "assistant_delta"
  | "assistant_end"
  | "tool_call"
  | "tool_result"
  | "system";

export type StreamEvent = {
  type?: StreamEventType;
  session_id?: string;

  delta_text?: string;
  content?: string;
  finish_reason?: string;
  tool_calls?: Array<{ id: string; name: string; arguments: string }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };

  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  tool_output?: string;
  is_error?: boolean;
  note?: string;

  message?: string;
  data?: Record<string, unknown>;
};

export type SessionMeta = {
  session_id: string;
  path: string;
  created_at: string;
  modified_at: string;
  message_count: number;
  preview: string;
};

export type Message = {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_call_id?: string;
  is_compaction?: boolean;
};

export type Config = {
  base_url: string | null;
  model: string | null;
  stream_enabled: boolean;
  skills_dirs: string[];
  tool_plugins: string[];
  api_key_masked: string | null;
};

export type BotMeta = {
  bot_id: string;
  name: string;
  is_default: boolean;
};

export type BotConfig = {
  bot_id: string;
  name: string;
  base_url: string | null;
  model: string | null;
  stream_enabled: boolean;
  api_key_masked: string | null;
  tool_plugins: string[];
  skills_disabled: string[];
  mcp_overrides: Record<string, boolean>;
  soul: string;
};

export type SkillInfo = {
  name: string;
  description: string;
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
