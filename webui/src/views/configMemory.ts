import type { Config } from "../lib/types";

export type MemoryBackend = "sqlite" | "postgres";

export type MemoryConfigDraft = {
  backend: MemoryBackend;
  databaseUrlConfigured: boolean;
  databaseUrlValue: string;
};

export function memoryDraftFromConfig(config: Config): MemoryConfigDraft {
  const backend = config.memory_backend === "postgres" ? "postgres" : "sqlite";
  const databaseUrlValue = config.memory_database_url_value ?? "";
  return {
    backend,
    databaseUrlConfigured: Boolean(config.memory_database_url_configured || databaseUrlValue),
    databaseUrlValue,
  };
}

export function buildMemoryConfigUpdate(draft: MemoryConfigDraft): {
  memory_backend: MemoryBackend;
  memory_database_url_value: string | null;
} {
  const trimmedUrl = draft.databaseUrlValue.trim();
  return {
    memory_backend: draft.backend,
    memory_database_url_value: trimmedUrl || null,
  };
}

export function memoryComparable(draft: MemoryConfigDraft | null): string {
  if (!draft) return "";
  return JSON.stringify({
    backend: draft.backend,
    databaseUrlConfigured: draft.databaseUrlConfigured,
    databaseUrlValue: draft.databaseUrlValue,
  });
}
