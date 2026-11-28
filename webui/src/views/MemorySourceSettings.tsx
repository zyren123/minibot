import { useEffect, useId, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";

import { getMemoryWorkspaceConfig, MemoryWorkspaceRequestError, updateMemoryWorkspaceConfig } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type {
  MemoryWorkspaceConfig,
  MemoryWorkspaceConnection,
  MemoryWorkspaceProvider,
  UpdateMemoryWorkspaceConfig,
} from "../lib/types";
import "./memorySourceSettings.css";

export const MEMORY_PROVIDERS: MemoryWorkspaceProvider[] = ["file", "novelmemo"];

export function novelMemoEndpoint(config?: MemoryWorkspaceConfig) {
  return config?.connection === "mcp" ? config.mcp_url : config?.vault_path;
}

export function memorySourceIdentity(config?: MemoryWorkspaceConfig) {
  return config?.provider === "novelmemo"
    ? JSON.stringify(["novelmemo", config.connection ?? "vault", novelMemoEndpoint(config)])
    : "file";
}

export function memorySourceSaveError(error: unknown, t: ReturnType<typeof useI18n>["t"]) {
  if (error instanceof MemoryWorkspaceRequestError && error.status === 502) {
    return t("memory.workspace.mcpUnavailable");
  }
  return error instanceof Error && error.message ? error.message : t("memory.workspace.connectionError");
}

function endpointLabel(config: MemoryWorkspaceConfig) {
  const endpoint = novelMemoEndpoint(config);
  if (!endpoint || config.connection !== "mcp") return endpoint;
  return endpoint.replace(/[?#].*$/, "");
}

export function MemorySourceEditor({
  config,
  fixedProvider,
  saving,
  error,
  onSave,
  onCancel,
}: {
  config: MemoryWorkspaceConfig;
  fixedProvider?: MemoryWorkspaceProvider;
  saving: boolean;
  error: string;
  onSave: (update: UpdateMemoryWorkspaceConfig) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const name = useId();
  const [provider, setProvider] = useState(fixedProvider ?? config.provider);
  const [connection, setConnection] = useState<MemoryWorkspaceConnection>(config.connection ?? "vault");
  const [vaultPath, setVaultPath] = useState(config.vault_path ?? "");
  const [mcpUrl, setMcpUrl] = useState(config.mcp_url ?? "");
  const remote = connection === "mcp";
  const novelMemo = provider === "novelmemo";
  const value = remote ? mcpUrl : vaultPath;
  const canSave = !saving && (!novelMemo || value.trim().length > 0);

  return (
    <form
      className="memory-source-form"
      aria-busy={saving}
      onSubmit={(event) => {
        event.preventDefault();
        if (!canSave) return;
        if (!novelMemo) onSave({ provider: "file" });
        else if (remote) onSave({ provider: "novelmemo", connection: "mcp", mcp_url: mcpUrl.trim() });
        else onSave({ provider: "novelmemo", connection: "vault", vault_path: vaultPath.trim() });
      }}
    >
      {fixedProvider === undefined && (
        <fieldset disabled={saving}>
          <legend>{t("memory.workspace.providerLabel")}</legend>
          <div className="memory-source-choices">
            {MEMORY_PROVIDERS.map((option) => (
              <label key={option}>
                <input
                  type="radio"
                  name={name}
                  checked={provider === option}
                  autoFocus={provider === option}
                  onChange={() => setProvider(option)}
                />
                {t(`memory.workspace.provider.${option}`)}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {novelMemo && (
        <>
          <label className="memory-source-field">
            {t("memory.workspace.connection")}
            <select
              autoFocus={fixedProvider !== undefined}
              value={connection}
              onChange={(event) => setConnection(event.target.value === "mcp" ? "mcp" : "vault")}
              disabled={saving}
            >
              <option value="vault">{t("memory.workspace.connection.vault")}</option>
              <option value="mcp">{t("memory.workspace.connection.mcp")}</option>
            </select>
          </label>
          <label className="memory-source-field">
            {t(remote ? "memory.workspace.mcpUrl" : "memory.workspace.vaultPath")}
            <input
              key={connection}
              type={remote ? "url" : "text"}
              value={value}
              onChange={(event) => (remote ? setMcpUrl : setVaultPath)(event.target.value)}
              disabled={saving}
              autoComplete="off"
              spellCheck={false}
              placeholder={remote ? "https://example.com/mcp" : t("memory.workspace.vaultPathExample")}
            />
          </label>
        </>
      )}
      {error && <p className="memory-source-error" role="alert">{error}</p>}
      <div className="memory-source-actions">
        <button type="button" className="chat-button chat-button-quiet" onClick={onCancel} disabled={saving}>
          {t("common.cancel")}
        </button>
        <button type="submit" className="chat-button chat-button-primary" disabled={!canSave}>
          {t(saving
            ? (novelMemo ? "memory.workspace.connecting" : "memory.workspace.saving")
            : fixedProvider === undefined ? "common.save" : "memory.workspace.connectAction")}
        </button>
      </div>
    </form>
  );
}

type SettingsRead =
  | { kind: "loading" }
  | { kind: "ready"; config: MemoryWorkspaceConfig }
  | { kind: "error"; message: string };

export function MemorySourceSettings({ botId }: { botId: string }) {
  const { t } = useI18n();
  const editorId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [read, setRead] = useState<SettingsRead>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void getMemoryWorkspaceConfig(botId, controller.signal).then(
      (config) => {
        if (!controller.signal.aborted) setRead({ kind: "ready", config });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setRead({ kind: "error", message: error instanceof Error ? error.message : "" });
        }
      },
    );
    return () => controller.abort();
  }, [attempt, botId]);

  function close() {
    setOpen(false);
    setError("");
    trigger.current?.focus();
  }

  async function save(update: UpdateMemoryWorkspaceConfig) {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      setRead({ kind: "ready", config: await updateMemoryWorkspaceConfig(botId, update) });
      close();
      setSaved(true);
    } catch (error) {
      setError(memorySourceSaveError(error, t));
    } finally {
      setSaving(false);
    }
  }

  if (read.kind !== "ready") {
    return (
      <div className="memory-source-settings">
        <div className="memory-source-settings-row">
          <span className="memory-source-settings-title">{t("memory.workspace.current")}</span>
          {read.kind === "loading" ? (
            <span className="memory-source-settings-detail" role="status">{t("common.loading")}</span>
          ) : (
            <>
              <span className="memory-source-settings-detail" role="alert">
                {read.message || t("memory.workspace.loadError")}
              </span>
              <button
                type="button"
                className="chat-button chat-button-quiet"
                onClick={() => {
                  setRead({ kind: "loading" });
                  setAttempt((value) => value + 1);
                }}
              >
                {t("memory.workspace.retry")}
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  const { config } = read;
  const detail = config.provider === "file"
    ? t("memory.workspace.fileDescription")
    : endpointLabel(config) || t("memory.workspace.needsConnection");

  return (
    <div
      className="memory-source-settings"
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open || saving) return;
        event.stopPropagation();
        close();
      }}
    >
      <div className="memory-source-settings-row">
        <span className="memory-source-settings-title">{t("memory.workspace.current")}</span>
        <div className="memory-source-settings-value">
          <span>{t(`memory.workspace.provider.${config.provider}`)}</span>
          <small className="memory-source-settings-detail">{detail}</small>
        </div>
        <button
          ref={trigger}
          type="button"
          className="chat-button chat-button-quiet memory-source-manage"
          aria-expanded={open}
          aria-controls={open ? editorId : undefined}
          aria-disabled={saving}
          onClick={() => {
            if (saving) return;
            setSaved(false);
            if (open) close();
            else setOpen(true);
          }}
        >
          {t("memory.workspace.manage")}
          <ChevronRight size={14} strokeWidth={1.65} aria-hidden="true" />
        </button>
      </div>
      {open && (
        <div id={editorId} className="memory-source-settings-editor">
          <MemorySourceEditor config={config} saving={saving} error={error} onSave={save} onCancel={close} />
        </div>
      )}
      {saved && <p className="sr-only" role="status">{t("memory.workspace.saved")}</p>}
    </div>
  );
}
