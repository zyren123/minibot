import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  createMemoryNamespace,
  getBotConfig,
  deleteMemoryNode,
  deleteMemoryNodes,
  getMemoryNode,
  getMemoryTree,
  getMemoryView,
  listMemoryNamespaces,
  searchMemory,
  updateMemoryNode,
  updateBotConfig,
  createMemoryNode,
} from "../lib/api";
import { useI18n } from "../lib/i18n";
import type {
  MemoryNamespace,
  MemoryNodeDetail,
  MemorySearchResult,
  MemorySystemView,
  MemorySystemViewName,
  MemoryTreeNode,
} from "../lib/types";
import { buildGraphLayout } from "./memoryGraph";

type Props = {
  botId: string;
};

type DetailState =
  | { mode: "system"; view: MemorySystemViewName; payload: MemorySystemView }
  | { mode: "node"; payload: MemoryNodeDetail }
  | null;

type ViewMode = "graph" | "tree";

type MemoryBootstrapSnapshot = {
  namespaces: MemoryNamespace[];
  activeNamespace: string | null;
  tree: MemoryTreeNode[];
  createOpen: boolean;
};

const GRAPH_COLUMN_GAP = 240;
const GRAPH_ROW_GAP = 148;
const GRAPH_PADDING = 72;
const GRAPH_NODE_WIDTH = 184;
const GRAPH_NODE_HEIGHT = 84;
const memoryBootstrapCache = new Map<string, Promise<MemoryBootstrapSnapshot>>();

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function slugify(value: string) {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (normalized) return normalized;
  return `novel-${Date.now().toString(36)}`;
}

async function fetchMemoryBootstrap(botId: string): Promise<MemoryBootstrapSnapshot> {
  const cached = memoryBootstrapCache.get(botId);
  if (cached) return cached;
  const pending = (async () => {
    const [config, namespaceList] = await Promise.all([getBotConfig(botId), listMemoryNamespaces(botId)]);
    if (namespaceList.length === 0) {
      return {
        namespaces: [],
        activeNamespace: null,
        tree: [],
        createOpen: true,
      };
    }
    const nextActive = config.active_memory_namespace ?? namespaceList[0]?.slug ?? null;
    if (!nextActive) {
      return {
        namespaces: namespaceList,
        activeNamespace: null,
        tree: [],
        createOpen: false,
      };
    }
    if (config.active_memory_namespace !== nextActive) {
      await updateBotConfig(botId, { active_memory_namespace: nextActive });
    }
    const treePayload = await getMemoryTree(botId);
    return {
      namespaces: namespaceList,
      activeNamespace: nextActive,
      tree: treePayload.nodes,
      createOpen: false,
    };
  })();
  memoryBootstrapCache.set(botId, pending);
  try {
    return await pending;
  } catch (error) {
    memoryBootstrapCache.delete(botId);
    throw error;
  }
}

function invalidateMemoryBootstrap(botId: string) {
  memoryBootstrapCache.delete(botId);
}

function NamespaceSelect(props: {
  namespaces: MemoryNamespace[];
  activeNamespace: string | null;
  disabled?: boolean;
  label: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="memory-field">
      <span className="memory-field-label">{props.label}</span>
      <select
        value={props.activeNamespace ?? ""}
        onChange={(event) => props.onChange(event.target.value)}
        disabled={props.disabled || props.namespaces.length === 0}
        className="memory-input"
      >
        {props.namespaces.length === 0 ? <option value="">{props.label}</option> : null}
        {props.namespaces.map((item) => (
          <option key={item.slug} value={item.slug}>
            {item.title}
          </option>
        ))}
      </select>
    </label>
  );
}

function ViewToggle(props: {
  value: ViewMode;
  onChange: (mode: ViewMode) => void;
  labels: { graph: string; tree: string };
}) {
  return (
    <div className="memory-segmented">
      {(["graph", "tree"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          className="memory-segmented-button"
          data-active={props.value === mode ? "true" : "false"}
          onClick={() => props.onChange(mode)}
        >
          {props.labels[mode]}
        </button>
      ))}
    </div>
  );
}

function SystemViewButtons(props: {
  active: MemorySystemViewName | null;
  onOpen: (view: MemorySystemViewName) => void;
  labels: Record<MemorySystemViewName, string>;
}) {
  return (
    <div className="memory-segmented">
      {(["boot", "index", "glossary"] as const).map((view) => (
        <button
          key={view}
          type="button"
          className="memory-segmented-button"
          data-active={props.active === view ? "true" : "false"}
          onClick={() => props.onOpen(view)}
        >
          {props.labels[view]}
        </button>
      ))}
    </div>
  );
}

function SearchResults(props: {
  results: MemorySearchResult[];
  selectedUri: string | null;
  emptyLabel: string;
  resultsLabel: string;
  onSelect: (uri: string) => void;
}) {
  return (
    <section className="memory-search-results">
      <div className="memory-panel-heading">
        <span>{props.resultsLabel}</span>
      </div>
      {props.results.length === 0 ? (
        <div className="memory-empty">{props.emptyLabel}</div>
      ) : (
        <div className="memory-result-list">
          {props.results.map((item) => (
            <button
              key={item.uri}
              type="button"
              className="memory-result-card"
              data-selected={props.selectedUri === item.uri ? "true" : "false"}
              onClick={() => props.onSelect(item.uri)}
            >
              <div className="memory-result-topline">
                <span className="memory-result-title">{item.title}</span>
                <span className="memory-kind-pill" data-kind={item.kind}>
                  {item.node_type ?? item.kind}
                </span>
              </div>
              <div className="memory-result-uri">{item.uri}</div>
              <div className="memory-result-snippet">{item.snippet}</div>
              <div className="memory-result-meta">match: {item.matched_by}</div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function TreeBranch(props: {
  nodes: MemoryTreeNode[];
  selectedUri: string | null;
  selectedBatchUris: string[];
  spacious?: boolean;
  emptyLabel: string;
  onSelect: (uri: string) => void;
  onToggleBatch: (uri: string) => void;
}) {
  if (props.nodes.length === 0) {
    return <div className="memory-empty">{props.emptyLabel}</div>;
  }

  return (
    <div className={props.spacious ? "memory-tree-branch-spacious" : "memory-tree-branch"}>
      {props.nodes.map((node) => (
        <div key={node.uri} className="memory-tree-node">
          <div className="memory-tree-row">
            <label className="memory-tree-check">
              <input
                type="checkbox"
                checked={props.selectedBatchUris.includes(node.uri)}
                onChange={() => props.onToggleBatch(node.uri)}
              />
            </label>
            <button
              type="button"
              className="memory-tree-button"
              data-selected={props.selectedUri === node.uri ? "true" : "false"}
              data-spacious={props.spacious ? "true" : "false"}
              data-kind={node.kind}
              onClick={() => props.onSelect(node.uri)}
            >
              <span className="memory-tree-titleblock">
                <span className="memory-tree-title">{node.title}</span>
                <span className="memory-tree-uri">{node.uri}</span>
              </span>
              <span className="memory-kind-pill" data-kind={node.kind}>
                {node.node_type ?? node.kind}
              </span>
            </button>
          </div>
          {node.children.length > 0 ? (
            <div className="memory-tree-children">
              <TreeBranch
                nodes={node.children}
                selectedUri={props.selectedUri}
                selectedBatchUris={props.selectedBatchUris}
                spacious={props.spacious}
                emptyLabel={props.emptyLabel}
                onSelect={props.onSelect}
                onToggleBatch={props.onToggleBatch}
              />
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function GraphCanvas(props: {
  tree: MemoryTreeNode[];
  selectedUri: string | null;
  title: string;
  emptyLabel: string;
  zoomInLabel: string;
  zoomOutLabel: string;
  resetLabel: string;
  onSelect: (uri: string) => void;
}) {
  const layout = useMemo(() => buildGraphLayout(props.tree), [props.tree]);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 40, y: 36 });
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null);

  useEffect(() => {
    setScale(1);
    setOffset({ x: 40, y: 36 });
  }, [props.tree]);

  useEffect(() => {
    function onPointerMove(event: PointerEvent) {
      if (!dragRef.current) return;
      setOffset({
        x: dragRef.current.originX + event.clientX - dragRef.current.startX,
        y: dragRef.current.originY + event.clientY - dragRef.current.startY,
      });
    }

    function onPointerUp() {
      dragRef.current = null;
    }

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, []);

  const positions = new Map(
    layout.nodes.map((node) => [
      node.uri,
      {
        left: GRAPH_PADDING + node.x * GRAPH_COLUMN_GAP,
        top: GRAPH_PADDING + node.y * GRAPH_ROW_GAP,
      },
    ]),
  );

  const width =
    layout.nodes.length === 0
      ? GRAPH_NODE_WIDTH + GRAPH_PADDING * 2
      : Math.max(...layout.nodes.map((node) => GRAPH_PADDING + node.x * GRAPH_COLUMN_GAP)) +
        GRAPH_NODE_WIDTH +
        GRAPH_PADDING;
  const height =
    layout.nodes.length === 0
      ? GRAPH_NODE_HEIGHT + GRAPH_PADDING * 2
      : Math.max(...layout.nodes.map((node) => GRAPH_PADDING + node.y * GRAPH_ROW_GAP)) +
        GRAPH_NODE_HEIGHT +
        GRAPH_PADDING;

  return (
    <section className="memory-panel memory-graph-panel">
      <div className="memory-panel-header">
        <div>
          <div className="memory-panel-heading">{props.title}</div>
          <div className="memory-panel-subheading">
            {layout.nodes.length} nodes / {layout.edges.length} edges
          </div>
        </div>
        <div className="memory-graph-controls">
          <button type="button" className="memory-control-button" onClick={() => setScale((value) => Math.max(0.55, value - 0.12))}>
            {props.zoomOutLabel}
          </button>
          <button type="button" className="memory-control-button" onClick={() => setScale((value) => Math.min(1.9, value + 0.12))}>
            {props.zoomInLabel}
          </button>
          <button type="button" className="memory-control-button" onClick={() => {
            setScale(1);
            setOffset({ x: 40, y: 36 });
          }}>
            {props.resetLabel}
          </button>
        </div>
      </div>
      {layout.nodes.length === 0 ? (
        <div className="memory-empty">{props.emptyLabel}</div>
      ) : (
        <div
          className="memory-graph-viewport"
          onPointerDown={(event) => {
            dragRef.current = {
              startX: event.clientX,
              startY: event.clientY,
              originX: offset.x,
              originY: offset.y,
            };
          }}
        >
          <div
            className="memory-graph-scene"
            style={{
              width,
              height,
              transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
            }}
          >
            <svg className="memory-graph-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMinYMin meet">
              {layout.edges.map((edge) => {
                const source = positions.get(edge.source);
                const target = positions.get(edge.target);
                if (!source || !target) return null;
                return (
                  <line
                    key={`${edge.source}->${edge.target}`}
                    className="memory-graph-edge"
                    x1={source.left + GRAPH_NODE_WIDTH / 2}
                    y1={source.top + GRAPH_NODE_HEIGHT / 2}
                    x2={target.left + GRAPH_NODE_WIDTH / 2}
                    y2={target.top + GRAPH_NODE_HEIGHT / 2}
                  />
                );
              })}
            </svg>
            {layout.nodes.map((node) => {
              const position = positions.get(node.uri);
              if (!position) return null;
              return (
                <button
                  key={node.uri}
                  type="button"
                  className="memory-graph-node"
                  data-kind={node.kind}
                  data-selected={props.selectedUri === node.uri ? "true" : "false"}
                  style={{
                    left: position.left,
                    top: position.top,
                    width: GRAPH_NODE_WIDTH,
                    height: GRAPH_NODE_HEIGHT,
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => props.onSelect(node.uri)}
                >
                  <span className="memory-graph-node-title">{node.title}</span>
                  <span className="memory-graph-node-uri">{node.uri}</span>
                  <span className="memory-kind-pill" data-kind={node.kind}>
                    {node.nodeType ?? node.kind}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

export default function MemoryView({ botId }: Props) {
  const { t } = useI18n();
  const hasInitializedRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [namespaces, setNamespaces] = useState<MemoryNamespace[]>([]);
  const [activeNamespace, setActiveNamespace] = useState<string | null>(null);
  const [tree, setTree] = useState<MemoryTreeNode[]>([]);
  const [selectedBatchUris, setSelectedBatchUris] = useState<string[]>([]);
  const [selectedUri, setSelectedUri] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailState>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("tree");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemorySearchResult[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [namespaceTitle, setNamespaceTitle] = useState("");
  const [namespaceSlug, setNamespaceSlug] = useState("");
  const [namespaceDescription, setNamespaceDescription] = useState("");
  const [editingNode, setEditingNode] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [creatingChild, setCreatingChild] = useState(false);
  const [childTitle, setChildTitle] = useState("");
  const [childSlug, setChildSlug] = useState("");
  const [childKind, setChildKind] = useState<"folder" | "memory">("memory");
  const [childContent, setChildContent] = useState("");

  const activeNamespaceRecord = useMemo(
    () => namespaces.find((item) => item.slug === activeNamespace) ?? null,
    [activeNamespace, namespaces],
  );

  function resetNodeForms() {
    setEditingNode(false);
    setCreatingChild(false);
    setEditTitle("");
    setEditContent("");
    setChildTitle("");
    setChildSlug("");
    setChildKind("memory");
    setChildContent("");
  }

  async function openSystemView(view: MemorySystemViewName) {
    const payload = await getMemoryView(botId, view);
    setDetail({ mode: "system", view, payload });
  }

  async function openNode(uri: string) {
    setBusy(true);
    setStatus(null);
    try {
      const payload = await getMemoryNode(botId, uri);
      setSelectedUri(uri);
      setDetail({ mode: "node", payload });
      setEditingNode(false);
      setCreatingChild(false);
      setEditTitle(payload.title);
      setEditContent(payload.content);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function loadWorkspace(options?: { preferredUri?: string | null; showView?: MemorySystemViewName; hydrateDetail?: boolean }) {
    setBusy(true);
    setStatus(null);
    try {
      const treePayload = await getMemoryTree(botId);
      setTree(treePayload.nodes);
      setSearchResults([]);
      setSelectedBatchUris([]);

      const preferredUri = options?.preferredUri ?? selectedUri;
      if (options?.hydrateDetail && preferredUri) {
        const nodePayload = await getMemoryNode(botId, preferredUri);
        setSelectedUri(preferredUri);
        setDetail({ mode: "node", payload: nodePayload });
        setEditTitle(nodePayload.title);
        setEditContent(nodePayload.content);
      } else if (options?.showView) {
        setSelectedUri(null);
        await openSystemView(options.showView);
      } else {
        setSelectedUri(null);
        setDetail(null);
        resetNodeForms();
      }
    } catch (error) {
      setStatus(String(error));
      setTree([]);
      setSelectedBatchUris([]);
      setSelectedUri(null);
      setDetail(null);
      resetNodeForms();
    } finally {
      setBusy(false);
    }
  }

  async function activateNamespace(slug: string, options?: { preferredUri?: string | null; showView?: MemorySystemViewName }) {
    setBusy(true);
    setStatus(null);
    try {
      await updateBotConfig(botId, { active_memory_namespace: slug });
      invalidateMemoryBootstrap(botId);
      setActiveNamespace(slug);
      await loadWorkspace({ preferredUri: options?.preferredUri ?? null, showView: options?.showView, hydrateDetail: false });
    } catch (error) {
      setStatus(String(error));
      setBusy(false);
    }
  }

  async function loadNamespaces() {
    setLoading(true);
    setStatus(null);
    try {
      const snapshot = await fetchMemoryBootstrap(botId);
      setNamespaces(snapshot.namespaces);
      setActiveNamespace(snapshot.activeNamespace);
      setTree(snapshot.tree);
      setSelectedBatchUris([]);
      setSelectedUri(null);
      setDetail(null);
      setCreateOpen(snapshot.createOpen);
      resetNodeForms();
    } catch (error) {
      setStatus(String(error));
      setNamespaces([]);
      setActiveNamespace(null);
      setTree([]);
      setSelectedBatchUris([]);
      setSelectedUri(null);
      setDetail(null);
      resetNodeForms();
    } finally {
      setLoading(false);
      setBusy(false);
    }
  }

  function toggleBatchUri(uri: string) {
    setSelectedBatchUris((current) =>
      current.includes(uri) ? current.filter((item) => item !== uri) : [...current, uri],
    );
  }

  async function submitNodeEdit() {
    if (detail?.mode !== "node") return;
    const nextTitle = editTitle.trim();
    if (!nextTitle) return;
    setBusy(true);
    setStatus(null);
    try {
      const updated = await updateMemoryNode(botId, {
        uri: detail.payload.uri,
        title: nextTitle,
        content: editContent,
      });
      invalidateMemoryBootstrap(botId);
      await loadWorkspace({ preferredUri: updated.uri, hydrateDetail: true });
      setEditingNode(false);
    } catch (error) {
      setStatus(String(error));
      setBusy(false);
    }
  }

  async function submitChildCreate() {
    if (detail?.mode !== "node") return;
    const nextTitle = childTitle.trim();
    if (!nextTitle) return;
    setBusy(true);
    setStatus(null);
    try {
      const created = await createMemoryNode(botId, {
        parent_uri: detail.payload.uri,
        slug: childSlug.trim() || undefined,
        title: nextTitle,
        kind: childKind,
        content: childContent,
      });
      invalidateMemoryBootstrap(botId);
      await loadWorkspace({ preferredUri: created.uri, hydrateDetail: true });
      setCreatingChild(false);
      setChildTitle("");
      setChildSlug("");
      setChildContent("");
      setChildKind("memory");
    } catch (error) {
      setStatus(String(error));
      setBusy(false);
    }
  }

  async function removeCurrentNode() {
    if (detail?.mode !== "node") return;
    if (!window.confirm(`Delete subtree ${detail.payload.uri}?`)) return;
    setBusy(true);
    setStatus(null);
    try {
      const fallbackUri = detail.payload.parent_uri ?? null;
      await deleteMemoryNode(botId, detail.payload.uri);
      invalidateMemoryBootstrap(botId);
      await loadWorkspace({ preferredUri: fallbackUri, hydrateDetail: Boolean(fallbackUri) });
    } catch (error) {
      setStatus(String(error));
      setBusy(false);
    }
  }

  async function removeSelectedNodes() {
    if (selectedBatchUris.length === 0) return;
    if (!window.confirm(`Delete ${selectedBatchUris.length} selected subtree(s)?`)) return;
    setBusy(true);
    setStatus(null);
    try {
      await deleteMemoryNodes(botId, selectedBatchUris);
      invalidateMemoryBootstrap(botId);
      await loadWorkspace({ preferredUri: null, hydrateDetail: false });
    } catch (error) {
      setStatus(String(error));
      setBusy(false);
    }
  }

  useEffect(() => {
    if (hasInitializedRef.current) return;
    hasInitializedRef.current = true;
    void loadNamespaces();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  const graphSubtitle = activeNamespaceRecord?.description || t("memory.header.subtitle");

  return (
    <div className="memory-shell">
      <section className="memory-hero">
        <div>
          <div className="memory-kicker">{activeNamespace ? `namespace://${activeNamespace}` : "memory://offline"}</div>
          <h1 className="memory-title">{t("memory.header.title")}</h1>
          <p className="memory-subtitle">{graphSubtitle}</p>
        </div>
        <div className="memory-toolbar">
          <NamespaceSelect
            namespaces={namespaces}
            activeNamespace={activeNamespace}
            disabled={busy || loading}
            label={t("memory.namespace.label")}
            onChange={(value) => {
              if (value && value !== activeNamespace) {
                void activateNamespace(value, { showView: "boot" });
              }
            }}
          />
          <ViewToggle
            value={viewMode}
            onChange={setViewMode}
            labels={{
              graph: t("memory.view.graph"),
              tree: t("memory.view.tree"),
            }}
          />
          <button
            type="button"
            className="memory-control-button"
            onClick={() => setCreateOpen((value) => !value)}
          >
            {t("common.create")}
          </button>
        </div>
      </section>

      {status ? <div className="memory-status-banner">{status}</div> : null}

      {createOpen ? (
        <section className="memory-create-panel">
          <div className="memory-panel-heading">{t("memory.namespace.createTitle")}</div>
          <div className="memory-panel-subheading">{t("memory.namespace.createSubtitle")}</div>
          <form
            className="memory-create-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!namespaceTitle.trim()) return;
              void (async () => {
                setBusy(true);
                setStatus(null);
                try {
                  const created = await createMemoryNamespace(botId, {
                    slug: namespaceSlug.trim() || slugify(namespaceTitle),
                    title: namespaceTitle.trim(),
                    description: namespaceDescription.trim() || null,
                  });
                  setNamespaceTitle("");
                  setNamespaceSlug("");
                  setNamespaceDescription("");
                  setCreateOpen(false);
                  const refreshed = await listMemoryNamespaces(botId);
                  setNamespaces(refreshed);
                  await activateNamespace(created.slug, { showView: "boot", preferredUri: null });
                } catch (error) {
                  setStatus(String(error));
                  setBusy(false);
                }
              })();
            }}
          >
            <label className="memory-field">
              <span className="memory-field-label">{t("memory.namespace.title")}</span>
              <input
                value={namespaceTitle}
                onChange={(event) => setNamespaceTitle(event.target.value)}
                placeholder={t("memory.namespace.titlePlaceholder")}
                className="memory-input"
                disabled={busy}
              />
            </label>
            <label className="memory-field">
              <span className="memory-field-label">{t("memory.namespace.slug")}</span>
              <input
                value={namespaceSlug}
                onChange={(event) => setNamespaceSlug(event.target.value)}
                placeholder={t("memory.namespace.slugPlaceholder")}
                className="memory-input"
                disabled={busy}
              />
            </label>
            <label className="memory-field memory-field-wide">
              <span className="memory-field-label">{t("memory.namespace.description")}</span>
              <textarea
                value={namespaceDescription}
                onChange={(event) => setNamespaceDescription(event.target.value)}
                placeholder={t("memory.namespace.descriptionPlaceholder")}
                className="memory-input memory-textarea"
                disabled={busy}
              />
            </label>
            <div className="memory-create-actions">
              <button type="submit" className="memory-primary-button" disabled={busy || !namespaceTitle.trim()}>
                {busy ? t("common.loading") : t("common.create")}
              </button>
            </div>
          </form>
        </section>
      ) : null}

      {loading ? (
        <div className="memory-empty">{t("common.loading")}</div>
      ) : namespaces.length === 0 ? (
        <div className="memory-empty">{t("memory.namespace.empty")}</div>
      ) : (
        <div className="memory-layout">
          <aside className="memory-panel memory-sidebar">
            <div className="memory-panel-header">
              <div>
                <div className="memory-panel-heading">{t("memory.explorer.title")}</div>
                <div className="memory-panel-subheading">root://</div>
              </div>
            </div>
            <div className="memory-sidebar-scroll">
              <div className="memory-sidebar-actions">
                <button
                  type="button"
                  className="memory-control-button memory-danger-button"
                  disabled={busy || selectedBatchUris.length === 0}
                  onClick={() => void removeSelectedNodes()}
                >
                  Delete Selected ({selectedBatchUris.length})
                </button>
              </div>
              <TreeBranch
                nodes={tree}
                selectedUri={selectedUri}
                selectedBatchUris={selectedBatchUris}
                emptyLabel={t("memory.explorer.empty")}
                onSelect={(uri) => void openNode(uri)}
                onToggleBatch={toggleBatchUri}
              />
            </div>
          </aside>

          <section className="memory-center">
            <div className="memory-panel memory-search-panel">
              <form
                className="memory-search-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!searchQuery.trim()) {
                    setSearchResults([]);
                    return;
                  }
                  void (async () => {
                    setBusy(true);
                    setStatus(null);
                    try {
                      const results = await searchMemory(botId, searchQuery.trim());
                      setSearchResults(results);
                    } catch (error) {
                      setStatus(String(error));
                    } finally {
                      setBusy(false);
                    }
                  })();
                }}
              >
                <input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder={t("memory.search.placeholder")}
                  className="memory-input memory-search-input"
                  disabled={busy}
                />
                <button type="submit" className="memory-primary-button" disabled={busy || !searchQuery.trim()}>
                  {t("common.search")}
                </button>
              </form>
              <SearchResults
                results={searchResults}
                selectedUri={selectedUri}
                emptyLabel={t("memory.search.empty")}
                resultsLabel={t("memory.search.results", { count: searchResults.length })}
                onSelect={(uri) => void openNode(uri)}
              />
            </div>

            {viewMode === "graph" ? (
              <GraphCanvas
                tree={tree}
                selectedUri={selectedUri}
                title={t("memory.graph.title")}
                emptyLabel={t("memory.graph.empty")}
                zoomInLabel={t("memory.graph.zoomIn")}
                zoomOutLabel={t("memory.graph.zoomOut")}
                resetLabel={t("memory.graph.reset")}
                onSelect={(uri) => void openNode(uri)}
              />
            ) : (
              <section className="memory-panel memory-tree-panel">
                <div className="memory-panel-header">
                  <div>
                    <div className="memory-panel-heading">{t("memory.tree.title")}</div>
                    <div className="memory-panel-subheading">{activeNamespaceRecord?.title ?? activeNamespace}</div>
                  </div>
                </div>
                <div className="memory-tree-canvas">
                  <TreeBranch
                    nodes={tree}
                    selectedUri={selectedUri}
                    selectedBatchUris={selectedBatchUris}
                    spacious
                    emptyLabel={t("memory.tree.empty")}
                    onSelect={(uri) => void openNode(uri)}
                    onToggleBatch={toggleBatchUri}
                  />
                </div>
              </section>
            )}
          </section>

          <aside className="memory-panel memory-detail-panel">
            <div className="memory-panel-header">
              <div>
                <div className="memory-panel-heading">{t("memory.detail.title")}</div>
                <div className="memory-panel-subheading">
                  {detail?.mode === "system"
                    ? `${t("memory.detail.systemView")} · ${detail.payload.uri}`
                    : detail?.mode === "node"
                      ? detail.payload.uri
                      : activeNamespaceRecord?.title ?? activeNamespace}
                </div>
              </div>
              <SystemViewButtons
                active={detail?.mode === "system" ? detail.view : null}
                labels={{
                  boot: t("memory.view.boot"),
                  index: t("memory.view.index"),
                  glossary: t("memory.view.glossary"),
                }}
                onOpen={(view) => void openSystemView(view)}
              />
            </div>

            {!detail ? (
              <div className="memory-empty">{t("memory.detail.empty")}</div>
            ) : detail.mode === "system" ? (
              <div className="memory-detail-scroll markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.payload.content}</ReactMarkdown>
              </div>
            ) : (
              <div className="memory-detail-scroll">
                <div className="memory-detail-grid">
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">{t("memory.namespace.label")}</span>
                    <span className="memory-detail-value">{activeNamespaceRecord?.title ?? activeNamespace}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">URI</span>
                    <span className="memory-detail-value">{detail.payload.uri}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">Parent URI</span>
                    <span className="memory-detail-value">{detail.payload.parent_uri ?? "-"}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">{t("memory.detail.kind")}</span>
                    <span className="memory-detail-value">{detail.payload.kind}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">{t("memory.detail.nodeType")}</span>
                    <span className="memory-detail-value">{detail.payload.node_type ?? "-"}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">{t("memory.detail.core")}</span>
                    <span className="memory-detail-value">{detail.payload.is_core ? "true" : "false"}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">{t("memory.detail.priority")}</span>
                    <span className="memory-detail-value">{detail.payload.priority}</span>
                  </div>
                  <div className="memory-detail-field">
                    <span className="memory-detail-label">{t("memory.detail.children")}</span>
                    <span className="memory-detail-value">{detail.payload.children.length}</span>
                  </div>
                </div>

                <div className="memory-detail-actions">
                  <button
                    type="button"
                    className="memory-control-button"
                    disabled={busy}
                    onClick={() => {
                      setEditTitle(detail.payload.title);
                      setEditContent(detail.payload.content);
                      setEditingNode((value) => !value);
                      setCreatingChild(false);
                    }}
                  >
                    {editingNode ? "Cancel Edit" : "Edit Node"}
                  </button>
                  <button
                    type="button"
                    className="memory-control-button"
                    disabled={busy}
                    onClick={() => {
                      setCreatingChild((value) => !value);
                      setEditingNode(false);
                    }}
                  >
                    {creatingChild ? "Cancel Child" : "Create Child"}
                  </button>
                  <button
                    type="button"
                    className="memory-control-button memory-danger-button"
                    disabled={busy}
                    onClick={() => void removeCurrentNode()}
                  >
                    Delete Subtree
                  </button>
                </div>

                {editingNode ? (
                  <form
                    className="memory-detail-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submitNodeEdit();
                    }}
                  >
                    <label className="memory-field">
                      <span className="memory-field-label">Title</span>
                      <input
                        value={editTitle}
                        onChange={(event) => setEditTitle(event.target.value)}
                        className="memory-input"
                        disabled={busy}
                      />
                    </label>
                    <label className="memory-field memory-field-wide">
                      <span className="memory-field-label">Content</span>
                      <textarea
                        value={editContent}
                        onChange={(event) => setEditContent(event.target.value)}
                        className="memory-input memory-textarea"
                        disabled={busy}
                      />
                    </label>
                    <div className="memory-create-actions">
                      <button type="submit" className="memory-primary-button" disabled={busy || !editTitle.trim()}>
                        Save Node
                      </button>
                    </div>
                  </form>
                ) : null}

                {creatingChild ? (
                  <form
                    className="memory-detail-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submitChildCreate();
                    }}
                  >
                    <div className="memory-detail-inline-grid">
                      <label className="memory-field">
                        <span className="memory-field-label">Title</span>
                        <input
                          value={childTitle}
                          onChange={(event) => setChildTitle(event.target.value)}
                          className="memory-input"
                          disabled={busy}
                        />
                      </label>
                      <label className="memory-field">
                        <span className="memory-field-label">Slug</span>
                        <input
                          value={childSlug}
                          onChange={(event) => setChildSlug(event.target.value)}
                          className="memory-input"
                          disabled={busy}
                        />
                      </label>
                      <label className="memory-field">
                        <span className="memory-field-label">Kind</span>
                        <select
                          value={childKind}
                          onChange={(event) => setChildKind(event.target.value as "folder" | "memory")}
                          className="memory-input"
                          disabled={busy}
                        >
                          <option value="memory">memory</option>
                          <option value="folder">folder</option>
                        </select>
                      </label>
                    </div>
                    <label className="memory-field memory-field-wide">
                      <span className="memory-field-label">Content</span>
                      <textarea
                        value={childContent}
                        onChange={(event) => setChildContent(event.target.value)}
                        className="memory-input memory-textarea"
                        disabled={busy}
                      />
                    </label>
                    <div className="memory-create-actions">
                      <button type="submit" className="memory-primary-button" disabled={busy || !childTitle.trim()}>
                        Create Child
                      </button>
                    </div>
                  </form>
                ) : null}

                <div className="memory-detail-section">
                  <div className="memory-detail-section-title">{t("memory.detail.triggers")}</div>
                  <div className="memory-chip-row">
                    {detail.payload.triggers.length > 0 ? (
                      detail.payload.triggers.map((trigger) => (
                        <span key={trigger} className="memory-chip">
                          {trigger}
                        </span>
                      ))
                    ) : (
                      <span className="memory-muted">{t("common.none")}</span>
                    )}
                  </div>
                </div>

                <div className="memory-detail-section">
                  <div className="memory-detail-section-title">Content</div>
                  <div className="markdown-content">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.payload.content}</ReactMarkdown>
                  </div>
                </div>

                <div className="memory-detail-section">
                  <div className="memory-detail-section-title">{t("memory.detail.children")}</div>
                  <div className="memory-child-list">
                    {detail.payload.children.length > 0 ? (
                      detail.payload.children.map((child) => (
                        <button
                          key={child.uri}
                          type="button"
                          className="memory-child-button"
                          onClick={() => void openNode(child.uri)}
                        >
                          <span className="memory-child-title">{child.title}</span>
                          <span className="memory-child-uri">{child.uri}</span>
                        </button>
                      ))
                    ) : (
                      <span className="memory-muted">{t("common.none")}</span>
                    )}
                  </div>
                </div>
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
