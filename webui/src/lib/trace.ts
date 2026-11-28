export type TraceKind = "message" | "model" | "tool" | "agent" | "wait" | "event";
export type TraceValue = string | number | boolean | null | TraceValue[] | { [key: string]: TraceValue };

export interface TraceField {
  label: string;
  source: string;
  value: TraceValue;
}

export interface TraceEvent {
  id: string;
  parent_id: string | null;
  kind: TraceKind;
  label: string;
  start_ms: number | null;
  duration_ms: number | null;
  status: string | null;
  is_error: boolean;
  fields: TraceField[];
  evidence: Record<string, TraceValue>;
}

export interface TraceRun {
  id: string;
  parent_run_id: string | null;
  user_message_id: string | null;
  title: string | null;
  started_at: string;
  duration_ms: number | null;
  status: string;
  events: TraceEvent[];
}

export interface TraceSession {
  schema_version: 1;
  source: { kind: string; id: string };
  session_id: string;
  recording: boolean;
  runs: TraceRun[];
}

function invalidTrace(): never {
  throw new Error("Invalid trace response");
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : invalidTrace();
}

function time(value: unknown): number | null {
  if (value === null) return null;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : invalidTrace();
}

function flag(value: unknown): boolean {
  return typeof value === "boolean" ? value : invalidTrace();
}

function items<T>(value: unknown, read: (element: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(read) : invalidTrace();
}

function isTraceValue(value: unknown): value is TraceValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  return Array.isArray(value)
    ? value.every(isTraceValue)
    : record(value) && Object.values(value).every(isTraceValue);
}

function traceValue(value: unknown): TraceValue {
  return isTraceValue(value) ? value : invalidTrace();
}

function traceField(value: unknown): TraceField {
  if (!record(value)) return invalidTrace();
  return { label: text(value.label), source: text(value.source), value: traceValue(value.value) };
}

function traceKind(value: unknown): TraceKind {
  switch (value) {
    case "message":
    case "model":
    case "tool":
    case "agent":
    case "wait":
    case "event":
      return value;
    default:
      return invalidTrace();
  }
}

function traceEvent(value: unknown): TraceEvent {
  if (!record(value) || !record(value.evidence)) return invalidTrace();
  return {
    id: text(value.id),
    parent_id: value.parent_id === null ? null : text(value.parent_id),
    kind: traceKind(value.kind),
    label: text(value.label),
    start_ms: time(value.start_ms),
    duration_ms: time(value.duration_ms),
    status: value.status === null ? null : text(value.status),
    is_error: flag(value.is_error),
    fields: items(value.fields, traceField),
    evidence: Object.fromEntries(Object.entries(value.evidence).map(([key, item]) => [key, traceValue(item)])),
  };
}

function uniqueIds<T extends { id: string }>(entries: T[]): T[] {
  if (new Set(entries.map((entry) => entry.id)).size !== entries.length) return invalidTrace();
  return entries;
}

function traceRun(value: unknown): TraceRun {
  if (!record(value)) return invalidTrace();
  return {
    id: text(value.id),
    parent_run_id: value.parent_run_id === null ? null : text(value.parent_run_id),
    user_message_id: value.user_message_id === null ? null : text(value.user_message_id),
    title: value.title === null ? null : text(value.title),
    started_at: text(value.started_at),
    duration_ms: time(value.duration_ms),
    status: text(value.status),
    events: uniqueIds(items(value.events, traceEvent)),
  };
}

export function parseTraceSession(value: unknown): TraceSession {
  if (!record(value) || value.schema_version !== 1 || !record(value.source)) return invalidTrace();
  return {
    schema_version: 1,
    source: { kind: text(value.source.kind), id: text(value.source.id) },
    session_id: text(value.session_id),
    recording: flag(value.recording),
    runs: uniqueIds(items(value.runs, traceRun)),
  };
}

export async function loadTraceSession(botId: string, sessionId: string, signal: AbortSignal): Promise<TraceSession> {
  const response = await fetch(
    `/api/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/trace`,
    { signal, cache: "no-store" },
  );
  if (!response.ok) throw new Error(response.statusText);
  const trace = parseTraceSession(await response.json());
  if (trace.session_id !== sessionId) return invalidTrace();
  return trace;
}

export interface TraceBranch<T> {
  item: T;
  children: TraceBranch<T>[];
}

export function traceForest<T extends { id: string }>(
  entries: readonly T[], parentId: (item: T) => string | null,
): TraceBranch<T>[] {
  const byId = new Map(entries.map((item) => [item.id, item]));
  const children = new Map<string, T[]>();
  const roots: T[] = [];
  for (const item of entries) {
    const parent = parentId(item);
    if (parent && parent !== item.id && byId.has(parent)) {
      const siblings = children.get(parent) ?? [];
      siblings.push(item);
      children.set(parent, siblings);
    } else {
      roots.push(item);
    }
  }
  const visited = new Set<string>();
  function visit(item: T): TraceBranch<T>[] {
    if (visited.has(item.id)) return [];
    visited.add(item.id);
    return [{ item, children: (children.get(item.id) ?? []).flatMap(visit) }];
  }
  return [...roots.flatMap(visit), ...entries.flatMap(visit)];
}

export interface TraceLane {
  agent: TraceEvent | null;
  events: TraceEvent[];
}

export function traceLanes(events: readonly TraceBranch<TraceEvent>[]): TraceLane[] {
  const root: TraceLane = { agent: null, events: [] };
  const lanes = [root];
  function visit(branch: TraceBranch<TraceEvent>, lane: TraceLane) {
    if (branch.item.kind === "agent") {
      const child: TraceLane = { agent: branch.item, events: [] };
      lanes.push(child);
      branch.children.forEach((event) => visit(event, child));
    } else {
      lane.events.push(branch.item);
      branch.children.forEach((event) => visit(event, lane));
    }
  }
  events.forEach((event) => visit(event, root));
  return lanes;
}
