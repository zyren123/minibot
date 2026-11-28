import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  ArrowLeft, Bot, ChartNoAxesGantt, Clock3, Cpu, ListTree, MessageSquare, PanelRightOpen, RefreshCw, Workflow, Wrench,
} from "lucide-react";

import { IconButton } from "../components/IconButton";
import { useI18n } from "../lib/i18n";
import {
  loadTraceSession, traceForest, traceLanes,
  type TraceBranch, type TraceEvent, type TraceKind, type TraceRun, type TraceSession, type TraceValue,
} from "../lib/trace";
import "./chatTrace.css";

type Layout = "waterfall" | "lanes";
type DetailOrigin = "row" | "plot";
type Selection = { kind: "run"; runId: string } | { kind: "event"; runId: string; eventId: string };
type Snapshot = { state: "loading" } | { state: "error" } | { state: "loaded"; trace: TraceSession; refreshingFailed: boolean };
type Translate = ReturnType<typeof useI18n>["t"];

const glyphs = {
  message: MessageSquare,
  model: Cpu,
  tool: Wrench,
  agent: Bot,
  wait: Clock3,
  event: Workflow,
} satisfies Record<TraceKind, typeof Workflow>;

const eventLabels = new Map([
  ["User", "chat.trace.user"], ["Assistant", "chat.trace.assistant"],
  ["Model", "chat.trace.model"], ["Wait for user", "chat.trace.wait"], ["Tool approval", "chat.trace.approval"],
  ["Delegate tasks", "chat.trace.delegateTasks"], ["Ask user", "chat.trace.askUser"],
  ["Call tools", "chat.trace.callTools"], ["Generate reply", "chat.trace.generateReply"],
  ["Artifact created", "chat.trace.artifactCreated"], ["Artifact search", "chat.trace.artifactSearch"],
  ["Artifact read", "chat.trace.artifactRead"],
]);

const fieldLabels = new Map([
  ["Message", "chat.trace.message"], ["Reasoning", "chat.trace.reasoning"], ["Input", "chat.trace.input"],
  ["Output", "chat.trace.output"], ["Prompt", "chat.trace.prompt"], ["Return", "chat.trace.return"],
  ["Question", "chat.trace.question"], ["Answer", "chat.trace.answer"],
]);

const statuses = new Map([
  ["incomplete", "chat.trace.incomplete"], ["running", "chat.trace.running"], ["waiting_for_user", "chat.trace.waiting"], ["completed", "chat.trace.completed"],
  ["resumed", "chat.trace.resumed"],
  ["success", "chat.trace.completed"], ["failed", "chat.trace.failed"], ["error", "chat.trace.failed"],
  ["interrupted", "chat.trace.interrupted"], ["budget_exceeded", "chat.trace.budgetExceeded"],
  ["context_window_exceeded", "chat.trace.contextExceeded"], ["refused", "chat.trace.refused"],
  ["timeout", "chat.trace.timeout"], ["invalid_arguments", "chat.trace.invalidArguments"],
]);

function translateLabel(label: string, labels: ReadonlyMap<string, string>, t: Translate) {
  const key = labels.get(label);
  return key ? t(key) : label;
}

function eventName(event: TraceEvent, t: Translate) {
  return event.kind === "tool" || event.kind === "agent" ? event.label : translateLabel(event.label, eventLabels, t);
}

function runName(run: TraceRun, t: Translate) {
  return run.title?.trim() || t(run.parent_run_id ? "chat.trace.resume" : "chat.trace.run");
}

function statusName(status: string | null, failed: boolean, t: Translate) {
  return failed ? t("chat.trace.failed") : status ? translateLabel(status, statuses, t) : null;
}

function formatTime(value: number | null) {
  if (value === null) return "—";
  if (value === 0) return "0 ms";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 1 : 2)} s`;
  return `${value.toFixed(value > 0 && value < 1 ? 2 : value < 10 ? 1 : 0)} ms`;
}

function chartDuration(run: TraceRun) {
  const maximum = run.events.reduce((max, event) => event.start_ms === null
    ? max : Math.max(max, event.start_ms + (event.duration_ms ?? 0)), run.duration_ms ?? 0);
  return maximum > 0 ? maximum : null;
}

function position(event: TraceEvent, duration: number | null): CSSProperties | null {
  if (event.start_ms === null || duration === null) return null;
  return {
    insetInlineStart: `${(event.start_ms / duration) * 100}%`,
    inlineSize: event.duration_ms !== null && event.duration_ms > 0 ? `${(event.duration_ms / duration) * 100}%` : undefined,
  };
}

function KindIcon({ kind }: { kind: TraceKind }) {
  const Icon = glyphs[kind];
  return <Icon aria-hidden="true" focusable="false" strokeWidth={1.7} size={15} className="trace-kind-icon" />;
}

function keyFor(selection: Selection) {
  return JSON.stringify(selection.kind === "run" ? [selection.runId] : [selection.runId, selection.eventId]);
}

const LIVE_REFRESH_MS = 1500;

interface TraceViewProps {
  botId: string;
  sessionId: string;
  sessionTitle: string;
  streaming: boolean;
  tooltipContainer?: HTMLElement | null;
}

export default function ChatTraceView({ botId, sessionId, sessionTitle, streaming, tooltipContainer }: TraceViewProps) {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState<Snapshot>({ state: "loading" });
  const [retries, setRetries] = useState(0);

  useEffect(() => {
    const request = new AbortController();
    let timer: number | undefined;
    async function read() {
      try {
        const trace = await loadTraceSession(botId, sessionId, request.signal);
        if (!request.signal.aborted) setSnapshot({ state: "loaded", trace, refreshingFailed: false });
      } catch {
        if (!request.signal.aborted) {
          setSnapshot((previous) => previous.state === "loaded"
            ? { ...previous, refreshingFailed: true }
            : { state: "error" });
        }
      } finally {
        if (!request.signal.aborted && streaming) timer = window.setTimeout(read, LIVE_REFRESH_MS);
      }
    }
    void read();
    return () => {
      request.abort();
      window.clearTimeout(timer);
    };
  }, [botId, sessionId, streaming, retries]);

  const retry = () => setRetries((revision) => revision + 1);
  if (snapshot.state !== "loaded") {
    const failed = snapshot.state === "error";
    return (
      <div className="trace-state">
        <p role="status">{t(failed ? "chat.trace.loadError" : "common.loading")}</p>
        {failed ? <IconButton icon={RefreshCw} label={t("common.refresh")} tooltipContainer={tooltipContainer} onClick={retry} /> : null}
      </div>
    );
  }
  if (!snapshot.trace.runs.length) {
    return (
      <div className="trace-state">
        <p role="status">{t(snapshot.refreshingFailed ? "chat.trace.loadError" : snapshot.trace.recording ? "chat.trace.empty" : "chat.trace.disabled")}</p>
        <IconButton icon={RefreshCw} label={t("common.refresh")} tooltipContainer={tooltipContainer} onClick={retry} />
      </div>
    );
  }
  return (
    <TraceTimeline
      trace={snapshot.trace}
      sessionTitle={sessionTitle}
      onRefresh={retry}
      refreshingFailed={snapshot.refreshingFailed}
      tooltipContainer={tooltipContainer}
    />
  );
}

interface TimelineProps {
  trace: TraceSession;
  sessionTitle: string;
  onRefresh: () => void;
  refreshingFailed: boolean;
  tooltipContainer?: HTMLElement | null;
}

function TraceTimeline({ trace, sessionTitle, onRefresh, refreshingFailed, tooltipContainer }: TimelineProps) {
  const { t } = useI18n();
  const [layout, setLayout] = useState<Layout>("waterfall");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [selection, setSelection] = useState<Selection | null>(null);
  const [sessionOpen, setSessionOpen] = useState(false);
  const sessionPanel = useId();
  const view = useRef<HTMLDivElement>(null);
  const previous = useRef<{ key: string; origin: DetailOrigin } | null>(null);
  const runs = useMemo(() => traceForest(trace.runs, (run) => run.parent_run_id), [trace.runs]);
  const run = selection ? trace.runs.find((candidate) => candidate.id === selection.runId) : null;
  const event = selection?.kind === "event" ? run?.events.find((candidate) => candidate.id === selection.eventId) : null;
  const showingDetail = !!(run && (selection?.kind === "run" || event));

  useLayoutEffect(() => {
    if (showingDetail) {
      view.current?.querySelector<HTMLElement>(".trace-detail-title")?.focus();
    } else if (previous.current) {
      const { key, origin } = previous.current;
      const candidates = Array.from(view.current?.querySelectorAll<HTMLElement>("[data-trace-detail]") ?? []);
      const matches = candidates.filter((control) => control.dataset.traceDetail === key);
      (matches.find((control) => control.dataset.traceOrigin === origin) ?? matches[0])?.focus();
      previous.current = null;
    }
  }, [showingDetail]);

  function toggle(id: string) {
    setExpanded((before) => {
      const next = new Set(before);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function openDetails(selected: Selection, origin: DetailOrigin) {
    previous.current = { key: keyFor(selected), origin };
    setSelection(selected);
  }

  const controls = { expanded, layout, onToggle: toggle, onDetails: openDetails, t, tooltipContainer };

  return (
    <div className="chat-trace" ref={view}>
      {showingDetail && run ? (
        <TraceDetails run={run} event={event} onBack={() => setSelection(null)} tooltipContainer={tooltipContainer} />
      ) : (
        <>
          <div className="trace-toolbar">
            <div role="group" aria-label={t("chat.trace.layout")} className="trace-layout">
              <IconButton icon={ListTree} label={t("chat.trace.waterfall")} aria-pressed={layout === "waterfall"} tooltipContainer={tooltipContainer} onClick={() => setLayout("waterfall")} />
              <IconButton icon={ChartNoAxesGantt} label={t("chat.trace.lanes")} aria-pressed={layout === "lanes"} tooltipContainer={tooltipContainer} onClick={() => setLayout("lanes")} />
            </div>
            {refreshingFailed ? <span className="trace-error" role="status">{t("chat.trace.loadError")}</span> : null}
            <IconButton icon={RefreshCw} label={t("common.refresh")} tooltipContainer={tooltipContainer} onClick={onRefresh} />
          </div>
          <button type="button" className="trace-row-button trace-session" aria-expanded={sessionOpen} aria-controls={sessionPanel} onClick={() => setSessionOpen(!sessionOpen)}>
            <Workflow aria-hidden="true" size={16} strokeWidth={1.7} />
            <span>{sessionTitle}</span>
          </button>
          <div id={sessionPanel} hidden={!sessionOpen}>
            {sessionOpen ? (
              <ul className="trace-list">
                {runs.map((branch) => <RunRow key={branch.item.id} branch={branch} depth={0} {...controls} />)}
              </ul>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

interface TraceControls {
  expanded: ReadonlySet<string>;
  layout: Layout;
  onToggle: (key: string) => void;
  onDetails: (selection: Selection, origin: DetailOrigin) => void;
  t: Translate;
  tooltipContainer?: HTMLElement | null;
}

function DetailsButton({ label, selection, controls }: { label: string; selection: Selection; controls: TraceControls }) {
  return (
    <IconButton
      icon={PanelRightOpen}
      label={controls.t("chat.trace.details", { title: label })}
      tooltipContainer={controls.tooltipContainer}
      data-trace-detail={keyFor(selection)}
      data-trace-origin="row"
      className="trace-details-button"
      onClick={() => controls.onDetails(selection, "row")}
    />
  );
}

function RunRow({ branch, depth, ...controls }: TraceControls & { branch: TraceBranch<TraceRun>; depth: number }) {
  const { item: run, children } = branch;
  const selection = { kind: "run", runId: run.id } satisfies Selection;
  const key = keyFor(selection);
  const open = controls.expanded.has(key);
  const id = useId();
  const title = runName(run, controls.t);
  const failed = run.status === "failed" || run.status === "error";
  const failures = run.events.filter((event) => event.is_error).length;
  const resumed = run.status === "waiting_for_user" && children.length > 0;
  const status = statusName(resumed ? "resumed" : run.status, false, controls.t);
  const events = useMemo(() => traceForest(run.events, (event) => event.parent_id), [run.events]);
  const duration = chartDuration(run);
  const waterfall = events.flatMap((event) =>
    run.user_message_id !== null && event.item.kind === "message" && event.item.evidence.message_id === run.user_message_id
      ? event.children : [event]);

  return (
    <li className="trace-run">
      <div className="trace-row trace-run-row" data-depth={depth > 0 ? "nested" : undefined} data-open={open} data-kind="message">
        <button type="button" className="trace-row-button" aria-expanded={open} aria-controls={id} onClick={() => controls.onToggle(key)}>
          <KindIcon kind="message" />
          <span className="trace-row-copy">
            <strong className="trace-run-name">{title}</strong>
            {status ? <small className={failed ? "trace-error" : undefined}>{status}</small> : null}
            {failures ? <small className="trace-error">{controls.t("chat.trace.failures", { count: failures })}</small> : null}
          </span>
          <span className="trace-duration">{formatTime(run.duration_ms)}</span>
        </button>
        <DetailsButton label={title} selection={selection} controls={controls} />
      </div>
      <div id={id} hidden={!open}>
        {open ? (
          <div className="trace-reveal">
            {controls.layout === "waterfall" ? (
              <ul className="trace-list trace-events" key="waterfall">
                {waterfall.map((event) => <EventRow key={event.item.id} branch={event} runId={run.id} depth={0} duration={duration} {...controls} />)}
              </ul>
            ) : (
              <LanePlot key="lanes" events={events} runId={run.id} duration={duration} {...controls} />
            )}
            {children.length ? (
              <ul className="trace-list">
                {children.map((segment) => <RunRow key={segment.item.id} branch={segment} depth={depth + 1} {...controls} />)}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

function EventRow({
  branch, runId, depth, duration, ...controls
}: TraceControls & { branch: TraceBranch<TraceEvent>; runId: string; depth: number; duration: number | null }) {
  const { item: event, children } = branch;
  const selection = { kind: "event", runId, eventId: event.id } satisfies Selection;
  const key = keyFor(selection);
  const label = eventName(event, controls.t);
  const status = statusName(event.status, event.is_error, controls.t);
  const open = controls.expanded.has(key);
  const id = useId();
  const bar = position(event, duration);
  const depthStyle: CSSProperties & { "--trace-depth": number } = { "--trace-depth": Math.min(depth, 5) };
  const rowContents = (
    <>
      <KindIcon kind={event.kind} />
      <span className="trace-row-copy">
        <span>{label}</span>
        {status ? <small className={event.is_error ? "trace-error" : undefined}>{status}</small> : null}
      </span>
      <span className="trace-duration">{formatTime(event.duration_ms)}</span>
    </>
  );

  return (
    <li>
      <div className="trace-row trace-event-row" data-kind={event.kind} data-error={event.is_error} data-open={open} style={depthStyle}>
        {children.length > 0 ? (
          <button type="button" className="trace-row-button" aria-expanded={open} aria-controls={id} onClick={() => controls.onToggle(key)}>
            {rowContents}
          </button>
        ) : (
          <div className="trace-row-label">{rowContents}</div>
        )}
        <DetailsButton label={label} selection={selection} controls={controls} />
        {bar ? <div className="trace-strip" aria-hidden="true"><span className="trace-mark" style={bar} /></div> : null}
      </div>
      {children.length > 0 ? (
        <div id={id} hidden={!open}>
          {open ? (
            <ul className="trace-list trace-reveal">
              {children.map((child) => <EventRow key={child.item.id} branch={child} runId={runId} depth={depth + 1} duration={duration} {...controls} />)}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function LanePlot({
  events, runId, duration, ...controls
}: TraceControls & { events: TraceBranch<TraceEvent>[]; runId: string; duration: number | null }) {
  const lanes = useMemo(() => traceLanes(events), [events]);
  return (
    <div className="trace-lanes-scroll" role="region" aria-label={controls.t("chat.trace.timeline")} tabIndex={0}>
      <div className="trace-lanes">
        {duration !== null ? (
          <div className="trace-axis" aria-hidden="true">
            <span />
            <div>{[0, 1, 2, 3, 4].map((tick) => <span key={tick}>{formatTime(duration * tick / 4)}</span>)}</div>
          </div>
        ) : null}
        {lanes.map((lane) => <LaneRow key={JSON.stringify(lane.agent?.id ?? null)} lane={lane} runId={runId} duration={duration} {...controls} />)}
      </div>
    </div>
  );
}

function LaneRow({
  lane, runId, duration, ...controls
}: TraceControls & { lane: ReturnType<typeof traceLanes>[number]; runId: string; duration: number | null }) {
  const { agent, events } = lane;
  const key = agent ? keyFor({ kind: "event", runId, eventId: agent.id }) : JSON.stringify([runId, null]);
  const open = controls.expanded.has(key);
  const id = useId();
  const label = agent ? eventName(agent, controls.t) : controls.t("chat.trace.parentAgent");
  const status = agent ? statusName(agent.status, agent.is_error, controls.t) : null;
  const span = agent ? position(agent, duration) : null;
  const laneMetric = agent?.is_error ? status : agent?.duration_ms == null ? status : formatTime(agent.duration_ms);

  return (
    <section className="trace-lane">
      <div className="trace-lane-row" data-open={open}>
        <div className="trace-lane-name" data-kind="agent" data-error={agent?.is_error}>
          <button type="button" className="trace-row-button" title={label} aria-expanded={open} aria-controls={id} onClick={() => controls.onToggle(key)}>
            <KindIcon kind="agent" />
            <span className="trace-row-copy">
              <strong>{label}</strong>
              {laneMetric ? <small className={agent?.is_error ? "trace-error" : "trace-duration"}>{laneMetric}</small> : null}
            </span>
          </button>
          {agent ? <DetailsButton label={label} selection={{ kind: "event", runId, eventId: agent.id }} controls={controls} /> : null}
        </div>
        <div className="trace-lane-track">
          {span ? <span className="trace-lane-span" style={span} data-kind="agent" data-error={agent?.is_error} aria-hidden="true" /> : null}
          {events.map((event) => {
            const style = position(event, duration);
            if (!style) return null;
            const name = eventName(event, controls.t);
            const selection = { kind: "event", runId, eventId: event.id } satisfies Selection;
            return (
              <button
                type="button"
                key={event.id}
                data-kind={event.kind}
                data-error={event.is_error}
                data-point={!event.duration_ms}
                data-trace-detail={keyFor(selection)}
                data-trace-origin="plot"
                className="trace-lane-mark"
                title={`${name} · ${formatTime(event.duration_ms)}`}
                aria-label={controls.t("chat.trace.details", { title: name })}
                style={style}
                onClick={() => controls.onDetails(selection, "plot")}
              />
            );
          })}
        </div>
      </div>
      <div id={id} hidden={!open}>
        {open ? (
          <ul className="trace-list trace-reveal">
            {events.map((item) => (
              <EventRow key={item.id} branch={{ item, children: [] }} runId={runId} depth={0} duration={null} {...controls} />
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}

function fieldBody(value: TraceValue) {
  if (typeof value === "string") return value || "—";
  return value === null ? "—" : JSON.stringify(value, null, 2);
}

function TraceDetails({
  run, event, onBack, tooltipContainer,
}: { run: TraceRun; event: TraceEvent | null | undefined; onBack: () => void; tooltipContainer?: HTMLElement | null }) {
  const { t } = useI18n();
  const titleId = useId();
  const duration = event ? event.duration_ms : run.duration_ms;
  const status = statusName(event ? event.status : run.status, !!event?.is_error, t);
  const fields = event?.fields ?? [];
  const evidence = event?.evidence ?? {
    run_id: run.id,
    parent_run_id: run.parent_run_id,
    user_message_id: run.user_message_id,
    started_at: run.started_at,
  };

  return (
    <section
      className="trace-detail trace-reveal"
      aria-labelledby={titleId}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onBack();
        }
      }}
    >
      <header className="trace-detail-header">
        <IconButton icon={ArrowLeft} label={t("chat.trace.back")} onClick={onBack} tooltipContainer={tooltipContainer} />
        <KindIcon kind={event?.kind ?? "message"} />
        <h2 id={titleId} className="trace-detail-title" tabIndex={-1}>{event ? eventName(event, t) : runName(run, t)}</h2>
      </header>
      <dl className="trace-detail-metrics">
        {status ? <div><dt>{t("chat.trace.status")}</dt><dd className={event?.is_error ? "trace-error" : undefined}>{status}</dd></div> : null}
        {event?.start_ms != null ? <div><dt>{t("chat.trace.offset")}</dt><dd>{formatTime(event.start_ms)}</dd></div> : null}
        {duration !== null ? <div><dt>{t("chat.trace.duration")}</dt><dd>{formatTime(duration)}</dd></div> : null}
      </dl>
      {fields.map((field, index) => (
        <section key={index} className="trace-detail-field">
          <h3>{translateLabel(field.label, fieldLabels, t)}</h3>
          <pre>{fieldBody(field.value)}</pre>
        </section>
      ))}
      <details className="trace-evidence">
        <summary>{t("chat.trace.source")}</summary>
        {fields.length > 0 ? (
          <dl>
            {fields.map((field, index) => (
              <div key={index}>
                <dt>{translateLabel(field.label, fieldLabels, t)}</dt>
                <dd>{field.source === "session" ? t("chat.trace.sessionSource") : field.source}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        <pre>{JSON.stringify(evidence, null, 2)}</pre>
      </details>
    </section>
  );
}
