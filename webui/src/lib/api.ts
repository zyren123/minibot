import type { Config, Message, SessionMeta, StreamEvent } from "./types";

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

export async function listSessions(): Promise<SessionMeta[]> {
  return apiGet<SessionMeta[]>("/api/sessions");
}

export async function createSession(): Promise<{ session_id: string }> {
  return apiPost<{ session_id: string }>("/api/sessions", {});
}

export async function deleteSession(sessionId: string): Promise<{ deleted: boolean }> {
  const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(await resp.text());
  return (await resp.json()) as { deleted: boolean };
}

export async function loadSession(sessionId: string): Promise<{ session_id: string; messages: Message[] }> {
  return apiGet<{ session_id: string; messages: Message[] }>(`/api/sessions/${encodeURIComponent(sessionId)}`);
}

export async function getConfig(): Promise<Config> {
  return apiGet<Config>("/api/config");
}

export async function updateConfig(body: Partial<Config> & { api_key?: string | null }): Promise<{ status: string }> {
  return apiPut<{ status: string }>("/api/config", body);
}

export async function cancelSession(sessionId: string): Promise<{ status: string }> {
  return apiPost<{ status: string }>(`/api/sessions/${encodeURIComponent(sessionId)}/cancel`, {});
}

export async function* streamChat(sessionId: string | null, prompt: string): AsyncGenerator<StreamEvent> {
  const resp = await fetch("/api/stream", {
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

