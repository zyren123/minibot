import type {
  AvailableModel,
  BotConfig,
  BotMeta,
  Config,
  DeletedMessageResult,
  DeletedModelsResult,
  DashboardData,
  FetchedModel,
  MCPServerInfo,
  Message,
  ProviderRecord,
  RegisteredModel,
  RegeneratedMessageResult,
  SessionData,
  SessionMeta,
  SkillInfo,
  SubagentCandidate,
  StreamEvent,
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

export async function listDashboard(): Promise<DashboardData> {
  return apiGet<DashboardData>("/api/dashboard");
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

export async function listMcpServers(): Promise<MCPServerInfo[]> {
  return apiGet<MCPServerInfo[]>("/api/mcp/servers");
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

export async function* streamChat(botId: string, sessionId: string | null, prompt: string): AsyncGenerator<StreamEvent> {
  const resp = await fetch(`/api/bots/${encodeURIComponent(botId)}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, prompt }),
  });
  if (!resp.ok) throw new Error(await resp.text());
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
