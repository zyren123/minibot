import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, BookOpen, Check, ChevronDown, ChevronRight, FileText, Folder, Pencil, Search, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { IconButton } from "../components/IconButton";
import {
  getMemoryWorkspaceConfig,
  getMemoryWorkspaceDirectory,
  getMemoryWorkspaceDocument,
  getMemoryWorkspaceGraph,
  MemoryWorkspaceRequestError,
  searchMemoryWorkspace,
  updateMemoryWorkspaceConfig,
  updateMemoryWorkspaceDocument,
} from "../lib/api";
import { useI18n } from "../lib/i18n";
import type {
  MemoryWorkspaceConfig,
  MemoryWorkspaceCrumb,
  MemoryWorkspaceDocument,
  MemoryWorkspaceEntry,
  MemoryWorkspaceProvider,
  UpdateMemoryWorkspaceConfig,
  UpdateMemoryWorkspaceDocument,
} from "../lib/types";
import {
  MEMORY_PROVIDERS,
  MemorySourceEditor,
  memorySourceIdentity,
  memorySourceSaveError,
  novelMemoEndpoint,
} from "./MemorySourceSettings";
import MemoryWorkspaceGraph from "./MemoryWorkspaceGraph";
import "./memoryWorkspace.css";

type Location = {
  path: string;
  documentId: string | null;
  folderDocument: boolean;
  previewOpen: boolean;
  query: string;
  search: string;
  view: "directory" | "graph";
};

type ReadState<T> =
  | { kind: "loading" }
  | { kind: "ready"; value: T }
  | { kind: "error"; message: string };

const FILE_PROVIDER = "file";
const NOVEL_PROVIDER = "novelmemo";
const LONG_TERM_DOCUMENT = "LONG_TERM.md";
const DAILY_DIRECTORY = "daily";

function initialLocation(provider: MemoryWorkspaceProvider, path = ""): Location {
  return {
    path,
    documentId: provider === FILE_PROVIDER && path === "" ? LONG_TERM_DOCUMENT : null,
    folderDocument: false,
    previewOpen: false,
    query: "",
    search: "",
    view: "directory",
  };
}

function displayTitle(
  { id, title }: MemoryWorkspaceCrumb,
  provider: MemoryWorkspaceProvider,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (provider === FILE_PROVIDER && id === LONG_TERM_DOCUMENT) return t("memory.workspace.longTerm");
  if (provider === FILE_PROVIDER && id === DAILY_DIRECTORY) return t("memory.workspace.daily");
  return title || t("app.tab.memory");
}

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function useRead<T>(load: (signal: AbortSignal) => Promise<T>, enabled = true): ReadState<T> {
  const [result, setResult] = useState<{ load: typeof load; state: ReadState<T> }>();
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void load(controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setResult({ load, state: { kind: "ready", value } });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({ load, state: { kind: "error", message: messageFor(error, "") } });
      },
    );
    return () => controller.abort();
  }, [enabled, load]);
  return enabled && result?.load === load ? result.state : { kind: "loading" };
}

function SourceControl({
  config,
  busy,
  onSelect,
  onConfigure,
}: {
  config: MemoryWorkspaceConfig;
  busy: boolean;
  onSelect: (provider: MemoryWorkspaceProvider) => void;
  onConfigure: () => void;
}) {
  const { t } = useI18n();
  const label = t(`memory.workspace.provider.${config.provider}`);
  const novelEndpoint = novelMemoEndpoint(config);
  const novelDetail = config.connection === "mcp"
    ? t("memory.workspace.connection.mcp")
    : config.vault_path?.split(/[\\/]/).filter(Boolean).at(-1) || t("memory.workspace.vaultType");
  const menuId = useId();
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const activeOption = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const keyboardOpening = useRef(false);

  useEffect(() => {
    if (!open) return;
    if (keyboardOpening.current) activeOption.current?.focus();
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !container.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  return (
    <div
      className="memory-source-control"
      ref={container}
      onBlur={(event) => {
        if (!event.relatedTarget || !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <span className="memory-source-context" aria-hidden="true">{t("memory.workspace.current")}</span>
      <button
        ref={trigger}
        type="button"
        className="memory-source-trigger"
        aria-label={t("memory.workspace.source", { source: label })}
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-disabled={busy}
        onClick={(event) => {
          if (busy) return;
          keyboardOpening.current = event.detail === 0;
          setOpen(!open);
        }}
      >
        {label}<ChevronDown size={13} strokeWidth={1.65} aria-hidden="true" />
      </button>
      {open && (
        <div id={menuId} className="memory-source-options" role="group" aria-label={t("memory.workspace.current")}>
          {MEMORY_PROVIDERS.map((provider) => {
            const current = provider === config.provider;
            const Glyph = provider === FILE_PROVIDER ? FileText : BookOpen;
            return (
              <button
                key={provider}
                ref={current ? activeOption : undefined}
                type="button"
                className="memory-source-option"
                aria-current={current ? "true" : undefined}
                onClick={() => {
                  setOpen(false);
                  trigger.current?.focus();
                  if (!current) onSelect(provider);
                }}
              >
                <Glyph size={17} strokeWidth={1.65} aria-hidden="true" />
                <span className="memory-source-option-copy">
                  <strong>{t(`memory.workspace.provider.${provider}`)}</strong>
                  <small>{provider === FILE_PROVIDER ? t("memory.workspace.fileDescription") : novelDetail}</small>
                </span>
                {current ? (
                  <span className="memory-source-current"><Check size={14} strokeWidth={1.8} aria-hidden="true" />{t("common.current")}</span>
                ) : provider === NOVEL_PROVIDER && !novelEndpoint ? (
                  <span className="memory-source-status">{t("memory.workspace.needsConnection")}</span>
                ) : null}
              </button>
            );
          })}
          {novelEndpoint && (
            <button
              className="memory-source-setting"
              type="button"
              onClick={() => {
                setOpen(false);
                trigger.current?.focus();
                onConfigure();
              }}
            >
              {t("memory.workspace.manageSource")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Notice({ message, retry }: { message: string; retry?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="memory-workspace-notice" role="alert">
      <p>{message || t("memory.workspace.loadError")}</p>
      {retry && <button type="button" className="chat-button" onClick={retry}>{t("memory.workspace.retry")}</button>}
    </div>
  );
}

function MemoryDocument({
  document,
  provider,
  onSave,
  onBack,
  onReload,
}: {
  document: MemoryWorkspaceDocument;
  provider: MemoryWorkspaceProvider;
  onSave: (update: UpdateMemoryWorkspaceDocument) => Promise<void>;
  onBack: () => void;
  onReload: () => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<{ content: string; disclosure: string; triggers: string; revision: string }>();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<{ message: string; conflict: boolean }>();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const title = displayTitle(document, provider, t);
  const isVault = provider === NOVEL_PROVIDER;
  const editing = draft !== undefined;

  useEffect(() => {
    if (editing) textarea.current?.focus();
  }, [editing]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || saving) return;
    setSaving(true);
    setSaveError(undefined);
    try {
      const disclosure = draft.disclosure.trim();
      const triggers = [...new Set(draft.triggers.split(/[\n,，]+/).map((term) => term.trim()).filter(Boolean))];
      await onSave({
        provider,
        path: document.id,
        content: draft.content,
        revision: draft.revision,
        ...(isVault && disclosure !== (document.disclosure ?? "") ? { disclosure } : {}),
        ...(isVault && draft.triggers !== document.triggers.join("\n") ? { triggers } : {}),
      });
      setDraft(undefined);
    } catch (error) {
      setSaveError({
        message: messageFor(error, t("memory.workspace.saveError")),
        conflict: error instanceof MemoryWorkspaceRequestError && error.status === 409,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="memory-document-bar">
        <IconButton icon={ArrowLeft} label={t("memory.workspace.back")} className="memory-mobile-back" onClick={onBack} />
        <span className="memory-document-path" title={document.filename}>{document.filename}</span>
        {!draft && (
          <IconButton
            icon={Pencil}
            label={t("memory.workspace.edit")}
            onClick={() => setDraft({
              content: document.content,
              disclosure: document.disclosure ?? "",
              triggers: document.triggers.join("\n"),
              revision: document.revision,
            })}
          />
        )}
      </div>
      <div className="memory-document-copy">
        <h1>{title}</h1>
        {draft ? (
          <form className="memory-document-editor" onSubmit={save}>
            <label>
              {t("memory.workspace.markdown")}
              <textarea
                ref={textarea}
                rows={12}
                value={draft.content}
                onChange={(event) => setDraft({ ...draft, content: event.target.value })}
                disabled={saving}
              />
            </label>
            {isVault && (
              <>
                <label>
                  {t("memory.workspace.disclosure")}
                  <input
                    value={draft.disclosure}
                    onChange={(event) => setDraft({ ...draft, disclosure: event.target.value })}
                    disabled={saving}
                  />
                </label>
                <label>
                  {t("memory.workspace.triggers")}
                  <textarea
                    rows={3}
                    value={draft.triggers}
                    onChange={(event) => setDraft({ ...draft, triggers: event.target.value })}
                    disabled={saving}
                    placeholder={t("memory.workspace.triggersHint")}
                  />
                </label>
              </>
            )}
            {saveError && (
              <Notice
                message={saveError.message}
                retry={saveError.conflict ? () => {
                  setDraft(undefined);
                  setSaveError(undefined);
                  onReload();
                } : undefined}
              />
            )}
            <div className="memory-workspace-actions">
              <button type="button" className="chat-button chat-button-quiet" disabled={saving} onClick={() => {
                setDraft(undefined);
                setSaveError(undefined);
              }}>{t("common.cancel")}</button>
              <button type="submit" className="chat-button chat-button-primary" disabled={saving}>
                {saving ? t("memory.workspace.saving") : t("common.save")}
              </button>
            </div>
          </form>
        ) : (
          <>
            {document.content.trim() ? (
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{document.content}</ReactMarkdown>
              </div>
            ) : <p className="memory-workspace-muted">{t("memory.workspace.emptyDocument")}</p>}
            {isVault && (document.disclosure || document.triggers.length > 0) && (
              <details className="memory-document-properties">
                <summary>{t("memory.workspace.properties")}</summary>
                <dl>
                  {document.disclosure && <><dt>{t("memory.workspace.disclosure")}</dt><dd>{document.disclosure}</dd></>}
                  {document.triggers.length > 0 && <><dt>{t("memory.workspace.triggers")}</dt><dd>{document.triggers.join(" · ")}</dd></>}
                </dl>
              </details>
            )}
          </>
        )}
      </div>
    </>
  );
}

function Breadcrumbs({
  provider,
  items,
  onOpen,
}: {
  provider: MemoryWorkspaceProvider;
  items: MemoryWorkspaceCrumb[];
  onOpen: (path: string) => void;
}) {
  const { t } = useI18n();
  return (
    <nav className="memory-workspace-path" aria-label={t("memory.workspace.path")}>
      <ol>
        {items.map((item, index) => {
          const label = displayTitle(item, provider, t);
          return (
            <li key={item.id}>
              {index > 0 && <ChevronRight size={13} strokeWidth={1.65} aria-hidden="true" />}
              {index === items.length - 1 ? (
                <span aria-current="page" title={label}>{label}</span>
              ) : (
                <button type="button" title={label} onClick={() => onOpen(item.id)}>{label}</button>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export default function MemoryWorkspaceView({
  botId,
  sourceSlot,
}: {
  botId: string;
  sourceSlot: HTMLDivElement | null;
}) {
  const { t } = useI18n();
  const connectionTitleId = useId();
  const connectionDialog = useRef<HTMLDialogElement>(null);
  const [reload, setReload] = useState(0);
  const [chosenConfig, setChosenConfig] = useState<MemoryWorkspaceConfig>();
  const [locations, setLocations] = useState<Partial<Record<string, Location>>>({});
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [configuring, setConfiguring] = useState(false);
  const [saved, setSaved] = useState<{ source: string; document: MemoryWorkspaceDocument }>();
  const fetchConfig = useCallback((signal: AbortSignal) => getMemoryWorkspaceConfig(botId, signal), [botId, reload]);
  const configRead = useRead(fetchConfig);
  const config = chosenConfig ?? (configRead.kind === "ready" ? configRead.value : undefined);
  const provider = config?.provider ?? FILE_PROVIDER;
  const isVault = provider === NOVEL_PROVIDER;
  const source = memorySourceIdentity(config);
  const location = locations[source] ?? initialLocation(provider);
  const readyToRead = Boolean(config);
  const fetchDirectory = useCallback(
    (signal: AbortSignal) => getMemoryWorkspaceDirectory(botId, provider, location.path, signal),
    [botId, provider, location.path, reload, source],
  );
  const directory = useRead(fetchDirectory, readyToRead);
  const fetchSearch = useCallback(
    (signal: AbortSignal) => searchMemoryWorkspace(botId, provider, location.search, signal),
    [botId, provider, location.search, reload, source],
  );
  const search = useRead(fetchSearch, readyToRead && isVault && Boolean(location.search));
  const fetchGraph = useCallback(
    (signal: AbortSignal) => getMemoryWorkspaceGraph(botId, provider, signal),
    [botId, provider, reload, source],
  );
  const graph = useRead(fetchGraph, readyToRead && isVault && location.view === "graph");
  const latestDaily = provider === FILE_PROVIDER && location.path === DAILY_DIRECTORY && directory.kind === "ready"
    ? directory.value.entries[0]
    : undefined;
  const documentId = location.documentId ?? latestDaily?.document_id ?? latestDaily?.id ?? null;
  const fetchDocument = useCallback(
    (signal: AbortSignal) => getMemoryWorkspaceDocument(botId, provider, documentId ?? "", signal),
    [botId, provider, documentId, reload, source],
  );
  const document = useRead(fetchDocument, readyToRead && location.view === "directory" && Boolean(documentId));
  const displayedDocument = saved?.source === source && saved.document.id === documentId
    ? saved.document
    : document.kind === "ready" ? document.value : undefined;

  useEffect(() => {
    const dialog = connectionDialog.current;
    if (configuring && dialog && !dialog.open) dialog.showModal();
  }, [configuring]);

  function move(update: Location) {
    setLocations((current) => ({ ...current, [source]: update }));
  }

  function openFolder(path: string, documentId: string | null = null) {
    setSaved(undefined);
    const next = initialLocation(provider, path);
    move({ ...next, documentId: documentId ?? next.documentId, folderDocument: documentId !== null });
  }

  function openEntry(entry: MemoryWorkspaceEntry) {
    if (entry.kind === "folder") {
      openFolder(entry.id, entry.document_id);
    } else {
      setSaved(undefined);
      move({ ...initialLocation(provider, entry.parent_id), documentId: entry.document_id ?? entry.id, previewOpen: true });
    }
  }

  function connectDialog() {
    setConnectionError("");
    setSwitchError("");
    setConfiguring(true);
  }

  async function switchProvider(update: UpdateMemoryWorkspaceConfig) {
    if (switching) return;
    setSwitching(true);
    setSwitchError("");
    setConnectionError("");
    try {
      const updated = await updateMemoryWorkspaceConfig(botId, update);
      setSaved(undefined);
      setChosenConfig(updated);
      connectionDialog.current?.close();
    } catch (error) {
      const message = memorySourceSaveError(error, t);
      if (connectionDialog.current?.open) setConnectionError(message);
      else setSwitchError(message);
    } finally {
      setSwitching(false);
    }
  }

  function selectProvider(next: MemoryWorkspaceProvider) {
    if (next === NOVEL_PROVIDER && !novelMemoEndpoint(config)) connectDialog();
    else void switchProvider({ provider: next });
  }

  function refresh() {
    setChosenConfig(undefined);
    setSaved(undefined);
    setReload((value) => value + 1);
  }

  async function saveDocument(update: UpdateMemoryWorkspaceDocument) {
    const value = await updateMemoryWorkspaceDocument(botId, update);
    setSaved({ source, document: value });
  }

  const displayedEntries = location.search ? search : directory;
  const entries = displayedEntries.kind === "ready"
    ? (Array.isArray(displayedEntries.value) ? displayedEntries.value : displayedEntries.value.entries)
    : [];
  const basicTitle = isVault
    ? (config?.connection === "mcp" ? undefined : config?.vault_path?.split(/[\\/]/).filter(Boolean).at(-1))
      ?? t("memory.workspace.provider.novelmemo")
    : t("app.tab.memory");
  const breadcrumbs = directory.kind === "ready"
    ? directory.value.breadcrumbs.map((crumb) => ({
      ...crumb, title: provider === FILE_PROVIDER && crumb.id === "" ? t("app.tab.memory") : crumb.title,
    }))
    : [{ id: "", title: basicTitle }];

  if (!config) {
    return (
      <section className="memory-workspace memory-workspace-start" aria-label={t("app.tab.memory")}>
        {configRead.kind === "error"
          ? <Notice message={configRead.message} retry={refresh} />
          : <p className="memory-workspace-muted" role="status">{t("common.loading")}</p>}
      </section>
    );
  }

  return (
    <section className="memory-workspace" aria-label={t("app.tab.memory")}>
      {sourceSlot && createPortal(
        <SourceControl config={config} busy={switching} onSelect={selectProvider} onConfigure={connectDialog} />,
        sourceSlot,
      )}
      {switchError && <Notice message={switchError} />}
      {isVault && (
        <div className="memory-vault-toolbar">
          <div className="memory-workspace-view-toggle" role="group" aria-label={t("memory.workspace.view")}>
            <button type="button" aria-pressed={location.view === "directory"} onClick={() => move({ ...location, view: "directory" })}>
              {t("memory.workspace.directory")}
            </button>
            <button type="button" aria-pressed={location.view === "graph"} onClick={() => move({ ...location, view: "graph" })}>
              {t("memory.workspace.graph")}
            </button>
          </div>
          <form
            className="memory-workspace-search"
            role="search"
            onSubmit={(event) => {
              event.preventDefault();
              move({ ...location, search: location.query.trim(), previewOpen: false, view: "directory" });
            }}
          >
            <Search size={15} strokeWidth={1.65} aria-hidden="true" />
            <input
              type="search"
              value={location.query}
              placeholder={t("memory.workspace.search")}
              aria-label={t("memory.workspace.search")}
              onChange={(event) => {
                const value = event.target.value;
                move({ ...location, query: value, search: value.trim() ? location.search : "" });
              }}
            />
            {location.search && (
              <IconButton
                icon={X}
                label={t("memory.workspace.clearSearch")}
                onClick={() => move({ ...location, search: "", query: "" })}
              />
            )}
            <button type="submit" className="sr-only">{t("common.search")}</button>
          </form>
        </div>
      )}
      {location.view === "graph" ? (
        graph.kind === "ready" ? (
          <MemoryWorkspaceGraph entries={graph.value} onOpen={openEntry} />
        ) : graph.kind === "error" ? (
          <Notice message={graph.message} retry={refresh} />
        ) : <p className="memory-workspace-muted memory-read-status" role="status">{t("common.loading")}</p>
      ) : (
        <div
          className="memory-workspace-layout"
          data-provider={provider}
          data-has-document={Boolean(documentId)}
          data-mobile-preview={Boolean(documentId && location.previewOpen)}
        >
          <section className="memory-workspace-directory" aria-label={t("memory.workspace.directory")}>
            <div className="memory-directory-bar">
              <Breadcrumbs provider={provider} items={breadcrumbs} onOpen={openFolder} />
              {location.folderDocument && (
                <IconButton
                  icon={FileText}
                  label={t("memory.workspace.openFolderDocument")}
                  className="memory-folder-document"
                  onClick={() => move({ ...location, previewOpen: true })}
                />
              )}
            </div>
            {location.search && <p className="memory-search-results-label">{t("memory.workspace.results")}</p>}
            {displayedEntries.kind === "error" ? (
              <Notice message={displayedEntries.message} retry={refresh} />
            ) : displayedEntries.kind === "loading" ? (
              <p className="memory-workspace-muted memory-read-status" role="status">{t("common.loading")}</p>
            ) : entries.length ? (
              <ul className="memory-workspace-rows">
                {entries.map((entry) => {
                  const label = displayTitle(entry, provider, t);
                  const EntryIcon = entry.kind === "folder" ? Folder : FileText;
                  const selected = entry.kind === "document" && (entry.document_id ?? entry.id) === documentId;
                  return (
                    <li key={entry.id}>
                      <button
                        type="button"
                        className="memory-workspace-row"
                        aria-current={selected ? "true" : undefined}
                        title={label}
                        onClick={() => openEntry(entry)}
                      >
                        <EntryIcon size={17} strokeWidth={1.65} aria-hidden="true" />
                        <span className="memory-workspace-row-label">
                          <span>{label}</span>
                          {location.search && entry.parent_id && <small>{entry.parent_id}</small>}
                        </span>
                        <ChevronRight size={13} strokeWidth={1.65} aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : <p className="memory-workspace-muted memory-read-status">
              {t(location.search ? "memory.workspace.emptySearch" : "memory.workspace.emptyDirectory")}
            </p>}
          </section>
          {(documentId || provider === FILE_PROVIDER) && (
            <section className="memory-workspace-document" aria-label={t("memory.workspace.preview")}>
              {displayedDocument ? (
                <MemoryDocument
                  key={`${source}:${displayedDocument.id}`}
                  document={displayedDocument}
                  provider={provider}
                  onSave={saveDocument}
                  onBack={() => move({ ...location, previewOpen: false })}
                  onReload={refresh}
                />
              ) : documentId ? (
                <>
                  <div className="memory-document-bar">
                    <IconButton
                      icon={ArrowLeft}
                      label={t("memory.workspace.back")}
                      className="memory-mobile-back"
                      onClick={() => move({ ...location, previewOpen: false })}
                    />
                  </div>
                  {document.kind === "error"
                    ? <Notice message={document.message} retry={refresh} />
                    : <p className="memory-workspace-muted memory-read-status" role="status">{t("common.loading")}</p>}
                </>
              ) : null}
            </section>
          )}
        </div>
      )}
      <dialog
        ref={connectionDialog}
        className="workspace-dialog memory-vault-dialog"
        aria-labelledby={connectionTitleId}
        onCancel={(event) => { if (switching) event.preventDefault(); }}
        onClose={() => setConfiguring(false)}
      >
        <h2 id={connectionTitleId} className="workspace-dialog-title">{t("memory.workspace.connectNovel")}</h2>
        {configuring && (
          <MemorySourceEditor
            config={config}
            fixedProvider={NOVEL_PROVIDER}
            saving={switching}
            error={connectionError}
            onSave={(update) => void switchProvider(update)}
            onCancel={() => connectionDialog.current?.close()}
          />
        )}
      </dialog>
    </section>
  );
}
