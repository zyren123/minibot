import type {
  AvailableModel,
  BotConfig,
  BotMeta,
  Config,
  DeletedMessageResult,
  DeletedModelsResult,
  MemoryGraphResponse,
  MemoryNamespace,
  MemoryNodeDetail,
  MemorySearchResult,
  MemorySystemView,
  MemorySystemViewName,
  MemoryTreeResponse,
  SkillDeleteResult,
  DashboardData,
  FetchedModel,
  MCPServerInfo,
  Message,
  PendingQuestion,
  PlatformConnection,
  ProviderRecord,
  ReasoningEffort,
  RegisteredModel,
  RegeneratedMessageResult,
  SessionData,
  SessionMeta,
  SkillInfo,
  SubagentCandidate,
  StreamEvent,
  UpdateMCPServerRequest,
} from "./types";

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as T;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as T;
}

export async function listBots(): Promise<BotMeta[]> {
  return apiGet<BotMeta[]>("/api/bots");
}

export async function createBot(name?: string): Promise<BotMeta> {
  return apiPost<BotMeta>("/api/bots", { name: name?.trim() || null });
}

export async function deleteBot(botId: string): Promise<{ deleted: boolean }> {
  const resp = await fetch(`/api/bots/${encodeURIComponent(botId)}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as { deleted: boolean };
}

export async function getBotConfig(botId: string): Promise<BotConfig> {
  return apiGet<BotConfig>(`/api/bots/${encodeURIComponent(botId)}/config`);
}

export async function updateBotConfig(
  botId: string,
  body: Partial<BotConfig> & { api_key?: string | null },
): Promise<{ status: string }> {
  return apiPut<{ status: string }>(`/api/bots/${encodeURIComponent(botId)}/config`, body);
}

export async function listMemoryNamespaces(botId: string): Promise<MemoryNamespace[]> {
  return apiGet<MemoryNamespace[]>(`/api/bots/${encodeURIComponent(botId)}/memory/namespaces`);
}

export async function createMemoryNamespace(
  botId: string,
  body: { slug: string; title: string; description?: string | null },
): Promise<MemoryNamespace> {
  return apiPost<MemoryNamespace>(`/api/bots/${encodeURIComponent(botId)}/memory/namespaces`, body);
}

export async function createMemoryNode(
  botId: string,
  body: {
    parent_uri?: string | null;
    slug?: string | null;
    title: string;
    kind?: "folder" | "memory";
    node_type?: string | null;
    content?: string;
    is_core?: boolean;
    priority?: number;
  },
): Promise<{ id: string; uri: string; title: string; kind: string; node_type: string | null; is_core: boolean; priority: number }> {
  return apiPost(`/api/bots/${encodeURIComponent(botId)}/memory/nodes`, body);
}

export async function getMemoryTree(botId: string): Promise<MemoryTreeResponse> {
  return apiGet<MemoryTreeResponse>(`/api/bots/${encodeURIComponent(botId)}/memory/tree`);
}

export async function getMemoryGraph(botId: string): Promise<MemoryGraphResponse> {
  return apiGet<MemoryGraphResponse>(`/api/bots/${encodeURIComponent(botId)}/memory/graph`);
}

export async function getMemoryNode(botId: string, uri: string): Promise<MemoryNodeDetail> {
  return apiGet<MemoryNodeDetail>(`/api/bots/${encodeURIComponent(botId)}/memory/node?uri=${encodeURIComponent(uri)}`);
}

export async function searchMemory(botId: string, query: string, limit = 8): Promise<MemorySearchResult[]> {
  return apiGet<MemorySearchResult[]>(
    `/api/bots/${encodeURIComponent(botId)}/memory/search?query=${encodeURIComponent(query)}&limit=${limit}`,
  );
}

export async function getMemoryView(botId: string, viewName: MemorySystemViewName): Promise<MemorySystemView> {
  return apiGet<MemorySystemView>(`/api/bots/${encodeURIComponent(botId)}/memory/views/${viewName}`);
}

export async function listDashboard(): Promise<DashboardData> {
  return apiGet<DashboardData>("/api/dashboard");
}

export async function listPlatforms(): Promise<PlatformConnection[]> {
  return apiGet<PlatformConnection[]>("/api/platforms");
}

export async function createPlatform(body: {
  name: string;
  kind?: "feishu" | "telegram" | "whatsapp";
  enabled?: boolean;
  bound_bot_id: string;
  app_id?: string;
  app_secret?: string;
}): Promise<PlatformConnection> {
  return apiPost<PlatformConnection>("/api/platforms", body);
}

export async function updatePlatform(
  platformId: string,
  body: {
    name?: string | null;
    enabled?: boolean | null;
    bound_bot_id?: string | null;
    app_id?: string | null;
    app_secret?: string | null;
  },
): Promise<PlatformConnection> {
  return apiPut<PlatformConnection>(`/api/platforms/${encodeURIComponent(platformId)}`, body);
}

export async function deletePlatform(platformId: string): Promise<{ deleted: boolean }> {
  const resp = await fetch(`/api/platforms/${encodeURIComponent(platformId)}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as { deleted: boolean };
}

export async function listAvailableModels(): Promise<AvailableModel[]> {
  return apiGet<AvailableModel[]>("/api/models/available");
}

export async function listSubagentCandidates(botId: string): Promise<SubagentCandidate[]> {
  return apiGet<SubagentCandidate[]>(`/api/bots/${encodeURIComponent(botId)}/subagent-candidates`);
}

export async function createProvider(body: {
  name: string;
  base_url: string;
  api_key?: string | null;
  enabled?: boolean;
}): Promise<ProviderRecord> {
  return apiPost<ProviderRecord>("/api/providers", body);
}

export async function updateProvider(
  providerId: string,
  body: { name?: string | null; base_url?: string | null; api_key?: string | null; enabled?: boolean | null },
): Promise<ProviderRecord> {
  return apiPut<ProviderRecord>(`/api/providers/${encodeURIComponent(providerId)}`, body);
}

export async function fetchProviderModels(providerId: string): Promise<FetchedModel[]> {
  return apiPost<FetchedModel[]>(`/api/providers/${encodeURIComponent(providerId)}/fetch-models`, {});
}

export async function createProviderModels(
  providerId: string,
  body: { model_names: string[]; added_via?: string },
): Promise<RegisteredModel[]> {
  return apiPost<RegisteredModel[]>(`/api/providers/${encodeURIComponent(providerId)}/models`, body);
}

export async function deleteProviderModels(
  providerId: string,
  body: { model_ids: string[] },
): Promise<DeletedModelsResult> {
  return apiPost<DeletedModelsResult>(`/api/providers/${encodeURIComponent(providerId)}/models/delete`, body);
}

export async function updateModel(
  modelId: string,
  body: { label?: string | null; enabled?: boolean | null },
): Promise<RegisteredModel> {
  return apiPut<RegisteredModel>(`/api/models/${encodeURIComponent(modelId)}`, body);
}

export async function listSkills(): Promise<SkillInfo[]> {
  return apiGet<SkillInfo[]>("/api/skills");
}

export async function createSkill(body: {
  name: string;
  description?: string | null;
  scope?: "user" | "project";
}): Promise<SkillInfo> {
  return apiPost<SkillInfo>("/api/skills", body);
}

export async function deleteSkill(scope: string, folderName: string): Promise<SkillDeleteResult> {
  const resp = await fetch(`/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(folderName)}`, {
    method: "DELETE",
  });
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as SkillDeleteResult;
}

export async function listMcpServers(): Promise<MCPServerInfo[]> {
  return apiGet<MCPServerInfo[]>("/api/mcp/servers");
}

export async function updateMcpServer(name: string, body: UpdateMCPServerRequest): Promise<MCPServerInfo> {
  return apiPut<MCPServerInfo>(`/api/mcp/servers/${encodeURIComponent(name)}`, body);
}

export async function connectMcpServer(name: string): Promise<MCPServerInfo> {
  return apiPost<MCPServerInfo>(`/api/mcp/servers/${encodeURIComponent(name)}/connect`, {});
}

export async function disconnectMcpServer(name: string): Promise<MCPServerInfo> {
  return apiPost<MCPServerInfo>(`/api/mcp/servers/${encodeURIComponent(name)}/disconnect`, {});
}

export async function listSessions(botId: string): Promise<SessionMeta[]> {
  return apiGet<SessionMeta[]>(`/api/bots/${encodeURIComponent(botId)}/sessions`);
}

export async function createSession(botId: string): Promise<{ session_id: string }> {
  return apiPost<{ session_id: string }>(`/api/bots/${encodeURIComponent(botId)}/sessions`, {});
}

export async function deleteSession(botId: string, sessionId: string): Promise<{ deleted: boolean }> {
  const resp = await fetch(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as { deleted: boolean };
}

export async function loadSession(
  botId: string,
  sessionId: string,
): Promise<SessionData> {
  return apiGet<SessionData>(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}`,
  );
}

export async function getPendingQuestion(botId: string, sessionId: string): Promise<PendingQuestion | null> {
  const resp = await fetch(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/pending-question`,
  );
  if (resp.status === 204) return null;
  if (!resp.ok) throw new Error(await resp.text());
  const payload = (await resp.json()) as PendingQuestion | null;
  return payload;
}

export async function submitPendingQuestionAnswer(
  botId: string,
  sessionId: string,
  body: { question_id: string; answer_text: string; selected_option_value?: string | null },
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/answer`,
    body,
  );
}

export async function* submitPendingQuestionAnswerStream(
  botId: string,
  sessionId: string,
  body: { question_id: string; answer_text: string; selected_option_value?: string | null },
): AsyncGenerator<StreamEvent> {
  const resp = await fetch(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/answer/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) throw new Error(await resp.text());
  yield* streamResponse(resp);
}

export async function deleteSessionMessage(
  botId: string,
  sessionId: string,
  messageId: string,
): Promise<DeletedMessageResult> {
  const resp = await fetch(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
    { method: "DELETE" },
  );
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as DeletedMessageResult;
}

export async function regenerateSessionMessage(
  botId: string,
  sessionId: string,
  messageId: string,
): Promise<RegeneratedMessageResult> {
  return apiPost<RegeneratedMessageResult>(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/regenerate`,
    {},
  );
}

async function* streamResponse(resp: Response): AsyncGenerator<StreamEvent> {
  if (!resp.body) return;

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    while (true) {
      const sep = buffer.indexOf("\n\n");
      if (sep === -1) break;
      const rawEvent = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      const lines = rawEvent.split("\n");
      let data = "";
      for (const line of lines) {
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      try {
        yield JSON.parse(data) as StreamEvent;
      } catch {
        yield { type: "system", message: "Failed to parse stream event.", data: { raw: data } };
      }
    }
  }
}

export async function getConfig(): Promise<Config> {
  return apiGet<Config>("/api/config");
}

export async function updateConfig(body: Partial<Config> & { api_key?: string | null }): Promise<{ status: string }> {
  return apiPut<{ status: string }>("/api/config", body);
}

export async function cancelSession(botId: string, sessionId: string): Promise<{ status: string }> {
  return apiPost<{ status: string }>(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/cancel`,
    {},
  );
}

export async function* streamChat(
  botId: string,
  sessionId: string | null,
  prompt: string,
  reasoningEffort?: ReasoningEffort | null,
): AsyncGenerator<StreamEvent> {
  const resp = await fetch(`/api/bots/${encodeURIComponent(botId)}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      prompt,
      reasoning_effort: reasoningEffort || null,
    }),
  });
  if (!resp.ok) throw new Error(await resp.text());
  yield* streamResponse(resp);
}

export async function* streamRegenerateSessionMessage(
  botId: string,
  sessionId: string,
  messageId: string,
  reasoningEffort?: ReasoningEffort | null,
): AsyncGenerator<StreamEvent> {
  const resp = await fetch(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/regenerate/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reasoning_effort: reasoningEffort || null,
      }),
    },
  );
  if (!resp.ok) throw new Error(await resp.text());
  yield* streamResponse(resp);
}
