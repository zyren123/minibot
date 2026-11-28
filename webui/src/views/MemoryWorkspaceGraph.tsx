import { useMemo, useRef, useState } from "react";
import { FileText, Folder, Minus, Plus, Scan } from "lucide-react";

import { IconButton } from "../components/IconButton";
import { useI18n } from "../lib/i18n";
import type { MemoryWorkspaceEntry } from "../lib/types";
import { buildGraphLayout, type MemoryTreeNode } from "./memoryGraph";

const COLUMN = 206;
const ROW = 64;
const START_X = 110;
const START_Y = 42;

function layoutEntries(entries: MemoryWorkspaceEntry[]) {
  const byParent = new Map<string, MemoryWorkspaceEntry[]>();
  for (const entry of entries) {
    const current = byParent.get(entry.parent_id);
    if (current) current.push(entry);
    else byParent.set(entry.parent_id, [entry]);
  }
  const visited = new Set<string>();
  function asTree(parent: string): MemoryTreeNode[] {
    return (byParent.get(parent) ?? []).flatMap((entry) => {
      if (visited.has(entry.id)) return [];
      visited.add(entry.id);
      return [{
        uri: entry.id,
        title: entry.title,
        kind: entry.kind,
        node_type: null,
        children: asTree(entry.id),
      }];
    });
  }
  return buildGraphLayout(asTree(""));
}

export default function MemoryWorkspaceGraph({
  entries,
  onOpen,
}: {
  entries: MemoryWorkspaceEntry[];
  onOpen: (entry: MemoryWorkspaceEntry) => void;
}) {
  const { t } = useI18n();
  const viewport = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const { nodes, edges } = useMemo(() => layoutEntries(entries), [entries]);
  const source = useMemo(() => new Map(entries.map((entry) => [entry.id, entry])), [entries]);
  const points = new Map(nodes.map((node) => [node.uri, {
    x: START_X + node.y * COLUMN,
    y: START_Y + node.x * ROW,
  }]));
  let width = START_X * 2;
  let height = START_Y * 2;
  for (const point of points.values()) {
    width = Math.max(width, point.x + START_X);
    height = Math.max(height, point.y + START_Y);
  }

  return (
    <section className="memory-workspace-graph" aria-label={t("memory.workspace.graph")}>
      <div className="memory-graph-actions">
        <IconButton icon={Minus} label={t("memory.graph.zoomOut")} disabled={scale <= 0.5} onClick={() => setScale((value) => Math.max(0.5, value - 0.125))} />
        <IconButton icon={Plus} label={t("memory.graph.zoomIn")} disabled={scale >= 2} onClick={() => setScale((value) => Math.min(2, value + 0.125))} />
        <IconButton icon={Scan} label={t("memory.graph.reset")} onClick={() => {
          setScale(1);
          viewport.current?.scrollTo({ left: 0, top: 0 });
        }} />
      </div>
      {nodes.length === 0 ? (
        <p className="memory-workspace-muted memory-read-status">{t("memory.workspace.emptyDirectory")}</p>
      ) : (
        <div
          className="memory-graph-scroll"
          tabIndex={0}
          ref={viewport}
          role="region"
          aria-label={t("memory.workspace.graph")}
        >
          <div className="memory-graph-footprint" style={{ width: width * scale, height: height * scale }}>
            <div className="memory-graph-map" style={{ width, height, transform: `scale(${scale})` }}>
              <svg className="memory-graph-links" width={width} height={height} aria-hidden="true">
                {edges.map((edge) => {
                  const from = points.get(edge.source);
                  const to = points.get(edge.target);
                  if (!from || !to) return null;
                  const middle = (from.x + to.x) / 2;
                  return <path key={edge.target} d={`M ${from.x} ${from.y} C ${middle} ${from.y}, ${middle} ${to.y}, ${to.x} ${to.y}`} />;
                })}
              </svg>
              {nodes.map((node) => {
                const point = points.get(node.uri);
                const entry = source.get(node.uri);
                if (!point || !entry) return null;
                const Glyph = entry.kind === "folder" ? Folder : FileText;
                return (
                  <button
                    key={entry.id}
                    className="memory-graph-entry"
                    type="button"
                    style={{ left: point.x, top: point.y }}
                    title={entry.title}
                    onClick={() => onOpen(entry)}
                  >
                    <Glyph size={15} strokeWidth={1.65} aria-hidden="true" />
                    <span>{entry.title}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
