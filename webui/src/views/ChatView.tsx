import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  AvailableModel,
  BotConfig,
  Message,
  PendingQuestion,
  ReasoningEffort,
  SessionMeta,
  StreamEvent,
  TodoSnapshot,
  Usage,
} from "../lib/types";
import {
  cancelSession,
  createSession,
  deleteSession,
  deleteSessionMessage,
  getBotConfig,
  getPendingQuestion,
  listAvailableModels,
  listSessions,
  loadSession,
  submitPendingQuestionAnswer,
  streamRegenerateSessionMessage,
  streamChat,
  updateBotConfig,
} from "../lib/api";
import { useI18n } from "../lib/i18n";
import {
  countHiddenTurnsBeforeIndex,
  expandHistoryWindowStart,
  findHistoryWindowStart,
} from "./chatHistoryWindow";

const INITIAL_VISIBLE_HISTORY_TURNS = 24;
const HISTORY_TURN_BATCH = 24;

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

function errorMessage(err: unknown) {
  if (err instanceof Error) return err.message;
  return String(err);
}

function hasVisibleAssistantContent(message: Message) {
  return Boolean(message.content?.trim() || message.reasoning?.trim());
}

function updateAssistantMessage(
  messages: Message[],
  messageId: string | undefined,
  updater: (current: Message) => Message,
  fallback?: () => Message,
) {
  if (messageId) {
    const index = messages.findIndex((item) => item.message_id === messageId);
    if (index >= 0) {
      const next = [...messages];
      next[index] = updater(next[index]);
      return next;
    }
  }

  const last = messages[messages.length - 1];
  if (last && last.role === "assistant") {
    const next = [...messages];
    next[next.length - 1] = updater(last);
    return next;
  }

  if (!fallback) return messages;
  return [...messages, fallback()];
}

function findAssistantTurnBounds(messages: Message[], messageId: string) {
  const assistantIndex = messages.findIndex((item) => item.message_id === messageId);
  if (assistantIndex < 0) return null;

  const assistantMessage = messages[assistantIndex];
  if (assistantMessage.role !== "assistant") return null;

  let userIndex = -1;
  const parentUserMessageId = assistantMessage.parent_user_message_id?.trim();
  if (parentUserMessageId) {
    for (let index = assistantIndex; index >= 0; index -= 1) {
      const candidate = messages[index];
      if (candidate.role !== "user") continue;
      if (candidate.message_id === parentUserMessageId) {
        userIndex = index;
        break;
      }
    }
  }

  if (userIndex < 0) {
    for (let index = assistantIndex; index >= 0; index -= 1) {
      if (messages[index].role === "user") {
        userIndex = index;
        break;
      }
    }
  }

  if (userIndex < 0) return null;

  let turnEnd = messages.length;
  for (let index = userIndex + 1; index < messages.length; index += 1) {
    if (messages[index].role === "user") {
      turnEnd = index;
      break;
    }
  }

  if (assistantIndex >= turnEnd) return null;
  return { userIndex, turnEnd };
}

function removeAssistantTurn(messages: Message[], messageId: string, preserveUserMessage: boolean) {
  const bounds = findAssistantTurnBounds(messages, messageId);
  if (!bounds) return messages;
  const turnStart = preserveUserMessage ? bounds.userIndex + 1 : bounds.userIndex;
  return [...messages.slice(0, turnStart), ...messages.slice(bounds.turnEnd)];
}

function cleanPendingChoiceLabel(value: string) {
  return value.replace(/\s+/g, " ").trim().replace(/[，,；;]+$/, "");
}

function derivePendingQuestionFromPrompt(question: PendingQuestion | null) {
  if (!question) return null;
  if (question.options.length > 0) return question;

  const text = question.prompt.trim();
  if (!text) return question;

  const matches = Array.from(text.matchAll(/(^|[\s\n])(\d+)[.)、．]\s*/g));
  if (matches.length < 2) return question;

  const points = matches.map((match) => {
    const start = match.index ?? 0;
    const marker = match[0] ?? "";
    return { start, contentStart: start + marker.length };
  });
  const intro = text.slice(0, points[0].start).trimEnd();
  if (!intro || !(intro.endsWith(":") || intro.endsWith("：") || intro.includes("\n"))) {
    return question;
  }

  const options = points
    .map((point, index) => {
      const end = index + 1 < points.length ? points[index + 1].start : text.length;
      const label = cleanPendingChoiceLabel(text.slice(point.contentStart, end));
      return label ? { label, value: label } : null;
    })
    .filter((option): option is { label: string; value: string } => Boolean(option && option.label.length <= 180));

  if (options.length < 2) return question;

  return {
    ...question,
    prompt: intro.endsWith(":") || intro.endsWith("：") ? intro.slice(0, -1).trimEnd() : intro,
    options,
  };
}

function usageParts(usage: Usage | null | undefined) {
  const total = usage?.total_tokens ?? null;
  const prompt = usage?.prompt_tokens ?? null;
  const completion = usage?.completion_tokens ?? null;
  if (total == null && prompt == null && completion == null) return null;
  return { total, prompt, completion };
}

function normalizeUsage(raw: unknown): Usage | null {
  if (!raw || typeof raw !== "object") return null;
  const usage = raw as Record<string, unknown>;
  const normalized: Usage = {};
  for (const key of ["prompt_tokens", "completion_tokens", "total_tokens"] as const) {
    const value = usage[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      normalized[key] = value;
    }
  }
  return Object.keys(normalized).length > 0 ? normalized : null;
}

type ContextSnapshot = {
  totalTokens: number;
  compacted: boolean;
};

function normalizeTodoSnapshot(raw: StreamEvent["todo"] | undefined): TodoSnapshot | null {
  if (!raw || typeof raw !== "object" || !Array.isArray(raw.items)) return null;
  const items: TodoSnapshot["items"] = [];
  raw.items.forEach((item, index) => {
    if (!item || typeof item !== "object") return;
    const id = typeof item.id === "string" && item.id.trim() ? item.id.trim() : `todo-${index + 1}`;
    const label = typeof item.label === "string" ? item.label.trim() : "";
    const status = item.status;
    if (!label || (status !== "pending" && status !== "active" && status !== "done")) return;
    const detail = typeof item.detail === "string" && item.detail.trim() ? item.detail.trim() : undefined;
    items.push(detail ? { id, label, status, detail } : { id, label, status });
  });
  const completed =
    typeof raw.completed === "number" && Number.isFinite(raw.completed)
      ? raw.completed
      : items.filter((item) => item.status === "done").length;
  const total = typeof raw.total === "number" && Number.isFinite(raw.total) ? raw.total : items.length;
  const title = typeof raw.title === "string" && raw.title.trim() ? raw.title.trim() : "Current plan";
  const completedAt = typeof raw.completed_at === "string" && raw.completed_at.trim() ? raw.completed_at : null;
  return {
    title,
    items,
    completed,
    total,
    visible: Boolean(raw.visible),
    completed_at: completedAt,
  };
}

function contextSnapshotFromMessages(messages: Message[]): ContextSnapshot | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const contextUsage = usageParts(message.context_usage);
    if (contextUsage?.total != null) {
      return {
        totalTokens: contextUsage.total,
        compacted: true,
      };
    }
    const usage = usageParts(message.usage);
    if (usage?.total != null) {
      return {
        totalTokens: usage.total,
        compacted: false,
      };
    }
  }
  return null;
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "true");
  area.style.position = "absolute";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  document.body.removeChild(area);
}

type ToolInvocationStatus = "running" | "done" | "error";

type ToolInvocationView = {
  key: string;
  toolCallId: string | null;
  name: string;
  argumentsText: string;
  outputText: string;
  status: ToolInvocationStatus;
};

type ToolRenderState = {
  assistantInvocationsByIndex: Map<number, ToolInvocationView[]>;
  linkedToolMessageIndexes: Set<number>;
  orphanInvocationsByIndex: Map<number, ToolInvocationView>;
};

function normalizeStreamToolCalls(
  toolCalls: StreamEvent["tool_calls"] | undefined,
): Message["tool_calls"] {
  if (!toolCalls?.length) return [];
  return toolCalls.map((toolCall) => ({
    id: toolCall.id,
    type: "function",
    function: {
      name: toolCall.name,
      arguments: toolCall.arguments,
    },
  }));
}

function formatToolArguments(raw: string | null | undefined) {
  const text = raw?.trim() ?? "";
  if (!text) return "";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function markdownElementChildren(node: unknown): Array<Record<string, unknown>> {
  if (!node || typeof node !== "object") return [];
  const children = (node as { children?: unknown }).children;
  if (!Array.isArray(children)) return [];
  return children.filter((child): child is Record<string, unknown> => Boolean(child) && typeof child === "object");
}

function markdownTagName(node: unknown): string | null {
  if (!node || typeof node !== "object") return null;
  const tagName = (node as { tagName?: unknown }).tagName;
  return typeof tagName === "string" ? tagName : null;
}

function getMarkdownTableColumnCount(node: unknown): number {
  for (const section of markdownElementChildren(node)) {
    const sectionTag = markdownTagName(section);
    if (sectionTag !== "thead" && sectionTag !== "tbody") continue;
    for (const row of markdownElementChildren(section)) {
      if (markdownTagName(row) !== "tr") continue;
      const cells = markdownElementChildren(row).filter((cell) => {
        const cellTag = markdownTagName(cell);
        return cellTag === "th" || cellTag === "td";
      });
      if (cells.length > 0) return cells.length;
    }
  }
  return 0;
}

function markdownWideTableStyle(columnCount: number): CSSProperties | undefined {
  if (columnCount < 4) return undefined;
  return {
    ["--markdown-table-columns" as "--markdown-table-columns"]: String(columnCount),
  } as CSSProperties;
}

function buildToolRenderState(messages: Message[]): ToolRenderState {
  const toolResultsByCallId = new Map<string, { index: number; message: Message }>();
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role !== "tool" || !message.tool_call_id) continue;
    toolResultsByCallId.set(message.tool_call_id, { index, message });
  }

  const assistantInvocationsByIndex = new Map<number, ToolInvocationView[]>();
  const linkedToolMessageIndexes = new Set<number>();
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role !== "assistant" || !message.tool_calls?.length) continue;

    const invocations = message.tool_calls.map((toolCall, toolIndex) => {
      const toolCallId = toolCall.id?.trim() || null;
      const toolName = toolCall.function?.name?.trim() || "";
      const result = toolCallId ? toolResultsByCallId.get(toolCallId) : undefined;
      if (result) linkedToolMessageIndexes.add(result.index);
      const status: ToolInvocationStatus = result ? (result.message.is_error ? "error" : "done") : "running";
      return {
        key: toolCallId ?? `${message.message_id ?? `assistant-${index}`}-tool-${toolIndex}`,
        toolCallId,
        name: toolName,
        argumentsText: formatToolArguments(toolCall.function?.arguments),
        outputText: result?.message.content ?? "",
        status,
      };
    });

    if (invocations.length > 0) {
      assistantInvocationsByIndex.set(index, invocations);
    }
  }

  const orphanInvocationsByIndex = new Map<number, ToolInvocationView>();
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role !== "tool" || linkedToolMessageIndexes.has(index)) continue;
    orphanInvocationsByIndex.set(index, {
      key: message.tool_call_id?.trim() || `tool-${index}`,
      toolCallId: message.tool_call_id ?? null,
      name: message.tool_name?.trim() || "",
      argumentsText: "",
      outputText: message.content ?? "",
      status: message.is_error ? "error" : "done",
    });
  }

  return { assistantInvocationsByIndex, linkedToolMessageIndexes, orphanInvocationsByIndex };
}

function statusClasses(status: ToolInvocationStatus) {
  if (status === "error") return "border-red-500/30 bg-red-500/10 text-red-200";
  if (status === "done") return "border-emerald-500/20 bg-emerald-500/10 text-emerald-200";
  return "border-amber-500/20 bg-amber-500/10 text-amber-200";
}

function ToolInvocationPanel(props: {
  invocation: ToolInvocationView;
  open: boolean;
  onToggle: () => void;
}) {
  const { invocation, open, onToggle } = props;
  const { t } = useI18n();
  const displayName = invocation.name || t("chat.tool.badge");

  function statusLabel(status: ToolInvocationStatus) {
    if (status === "error") return t("chat.tool.status.error");
    if (status === "done") return t("chat.tool.status.done");
    return t("chat.tool.status.running");
  }

  return (
    <div className="w-full rounded-2xl border border-zinc-800 bg-zinc-950/80 shadow-[0_10px_30px_rgba(0,0,0,0.18)]">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        onClick={onToggle}
        aria-expanded={open}
      >
        <div className="flex min-w-0 items-center gap-3">
          <span className="rounded-full bg-zinc-900 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
            {t("chat.tool.badge")}
          </span>
          <span className="truncate text-sm font-semibold text-zinc-100">{displayName}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={classNames(
              "rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
              statusClasses(invocation.status),
            )}
          >
            {statusLabel(invocation.status)}
          </span>
          <span className="text-sm text-zinc-500">{open ? "▾" : "▸"}</span>
        </div>
      </button>

      {open ? (
        <div className="border-t border-zinc-800 px-4 py-4">
          <div className="space-y-4">
            <section>
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
                {t("chat.tool.arguments")}
              </div>
              {invocation.argumentsText ? (
                <pre className="max-h-72 overflow-auto rounded-2xl bg-zinc-900 px-4 py-3 text-xs leading-relaxed text-zinc-200">
                  {invocation.argumentsText}
                </pre>
              ) : (
                <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-3 text-xs text-zinc-500">
                  {t("chat.tool.noArguments")}
                </div>
              )}
            </section>

            <section>
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
                {t("chat.tool.result")}
              </div>
              {invocation.outputText ? (
                <pre className="max-h-96 overflow-auto rounded-2xl bg-zinc-900 px-4 py-3 text-xs leading-relaxed text-zinc-200">
                  {invocation.outputText}
                </pre>
              ) : (
                <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-3 text-xs text-zinc-500">
                  {invocation.status === "running" ? t("chat.tool.waiting") : t("chat.tool.noResult")}
                </div>
              )}
            </section>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function ChatView(props: { botId: string; botName?: string }) {
  const { botId, botName } = props;
  const { language, t } = useI18n();

  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [visibleHistoryStartIndex, setVisibleHistoryStartIndex] = useState(0);
  const [contextBySession, setContextBySession] = useState<Record<string, ContextSnapshot>>({});
  const [prompt, setPrompt] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [botConfig, setBotConfig] = useState<BotConfig | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort | "">("");
  const [modelSaving, setModelSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [pendingDeleteSessionId, setPendingDeleteSessionId] = useState<string | null>(null);
  const [messageActionId, setMessageActionId] = useState<string | null>(null);
  const [reasoningOpenById, setReasoningOpenById] = useState<Record<string, boolean>>({});
  const [toolOpenById, setToolOpenById] = useState<Record<string, boolean>>({});
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSessionsOpen, setMobileSessionsOpen] = useState(false);
  const [mobileTodoOpen, setMobileTodoOpen] = useState(false);
  const [todoSnapshot, setTodoSnapshot] = useState<TodoSnapshot | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [pendingAnswerDraft, setPendingAnswerDraft] = useState("");
  const [pendingCustomOpen, setPendingCustomOpen] = useState(false);
  const [submittingAnswer, setSubmittingAnswer] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const sessionStateVersionRef = useRef(0);

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.modified_at.localeCompare(a.modified_at)),
    [sessions],
  );

  const selectedModel = useMemo(
    () => availableModels.find((item) => item.model_id === botConfig?.chat_model_id) ?? null,
    [availableModels, botConfig?.chat_model_id],
  );
  const activeContextSnapshot = useMemo(() => {
    if (!sessionId) return null;
    return contextBySession[sessionId] ?? contextSnapshotFromMessages(messages);
  }, [contextBySession, messages, sessionId]);
  const sessionLocale = language === "en" ? "en-US" : "zh-CN";
  const contextThresholdTokens = botConfig?.auto_compact_threshold_tokens ?? null;
  const todoVisible = Boolean(todoSnapshot?.visible && todoSnapshot.items.length);
  const todoPercent =
    todoSnapshot && todoSnapshot.total > 0 ? Math.min(100, Math.round((todoSnapshot.completed / todoSnapshot.total) * 100)) : 0;
  const todoDone = Boolean(todoSnapshot && todoSnapshot.total > 0 && todoSnapshot.completed >= todoSnapshot.total);
  const activeTodoItem = todoSnapshot?.items.find((item) => item.status === "active") ?? null;
  const todoCompletedAtLabel = useMemo(() => {
    if (!todoSnapshot?.completed_at) return null;
    const parsed = new Date(todoSnapshot.completed_at);
    if (Number.isNaN(parsed.getTime())) return null;
    return new Intl.DateTimeFormat(sessionLocale, {
      hour: "numeric",
      minute: "2-digit",
    }).format(parsed);
  }, [sessionLocale, todoSnapshot?.completed_at]);
  const contextPercent =
    activeContextSnapshot && contextThresholdTokens
      ? Math.min(100, Math.round((activeContextSnapshot.totalTokens / contextThresholdTokens) * 100))
      : null;
  const contextRatio =
    activeContextSnapshot && contextThresholdTokens ? activeContextSnapshot.totalTokens / contextThresholdTokens : null;
  const contextTone =
    contextRatio == null
      ? "idle"
      : activeContextSnapshot?.compacted
        ? "compacted"
        : contextRatio >= 1
          ? "danger"
          : contextRatio >= 0.82
            ? "warning"
            : "stable";
  const contextStatusKey =
    contextTone === "compacted"
      ? "chat.context.compacted"
      : contextTone === "danger"
        ? "chat.context.compacting"
        : contextTone === "warning"
          ? "chat.context.warning"
          : contextTone === "stable"
            ? "chat.context.stable"
            : "chat.context.empty";

  const latestRegeneratableAssistantId = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role !== "assistant") continue;
      if (!message.message_id || !message.parent_user_message_id) continue;
      return message.message_id;
    }
    return null;
  }, [messages]);

  const toolRenderState = useMemo(() => buildToolRenderState(messages), [messages]);
  const hiddenHistoryTurnCount = useMemo(
    () => countHiddenTurnsBeforeIndex(messages, visibleHistoryStartIndex),
    [messages, visibleHistoryStartIndex],
  );
  const visibleMessageEntries = useMemo(
    () =>
      messages.slice(visibleHistoryStartIndex).map((message, offset) => ({
        message,
        index: visibleHistoryStartIndex + offset,
      })),
    [messages, visibleHistoryStartIndex],
  );

  const chatBusy = streaming || submittingAnswer;
  const chatBlocked = Boolean(botConfig && (!botConfig.enabled || !botConfig.chat_ready));
  const blockedReason =
    botConfig?.chat_disabled_reason ?? (botConfig?.enabled === false ? t("chat.fallbackDisabledReason") : null);
  const awaitingUserAnswer = Boolean(pendingQuestion);
  const pendingQuestionMessageId = pendingQuestion?.message_id ?? null;
  const showPendingQuestionFallback = Boolean(
    pendingQuestion && (!pendingQuestionMessageId || !messages.some((item) => item.role === "assistant" && item.message_id === pendingQuestionMessageId)),
  );

  function clearTodoState() {
    setTodoSnapshot(null);
    setMobileTodoOpen(false);
  }

  function markSessionStateDirty() {
    sessionStateVersionRef.current += 1;
  }

  function syncContextSnapshot(targetSessionId: string, nextMessages: Message[]) {
    setContextBySession((prev) => {
      const next = { ...prev };
      const snapshot = contextSnapshotFromMessages(nextMessages);
      if (snapshot) {
        next[targetSessionId] = snapshot;
      } else {
        delete next[targetSessionId];
      }
      return next;
    });
  }

  async function refreshSessions(nextBotId: string = botId) {
    const list = await listSessions(nextBotId);
    setSessions(list);
    setSessionId((prev) => {
      if (prev && list.some((item) => item.session_id === prev)) return prev;
      return list.length > 0 ? list[0].session_id : null;
    });
  }

  async function refreshBotState(nextBotId: string = botId) {
    const [cfg, models] = await Promise.all([getBotConfig(nextBotId), listAvailableModels()]);
    setBotConfig(cfg);
    setAvailableModels(models);
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const created = await createSession(botId);
    setReasoningOpenById({});
    setToolOpenById({});
    clearTodoState();
    setSessionId(created.session_id);
    await refreshSessions(botId);
    return created.session_id;
  }

  async function selectSession(id: string, options?: { force?: boolean }) {
    const expectedVersion = sessionStateVersionRef.current;
    const [sess, question] = await Promise.all([loadSession(botId, id), getPendingQuestion(botId, id)]);
    if (sessionIdRef.current !== id) return;
    if (!options?.force && sessionStateVersionRef.current !== expectedVersion) return;
    setReasoningOpenById({});
    setToolOpenById({});
    clearTodoState();
    setMessages(sess.messages);
    setVisibleHistoryStartIndex(findHistoryWindowStart(sess.messages, INITIAL_VISIBLE_HISTORY_TURNS));
    syncContextSnapshot(id, sess.messages);
    setPendingQuestion(derivePendingQuestionFromPrompt(question));
    if (!question) {
      setPendingAnswerDraft("");
      setPendingCustomOpen(false);
    }
  }

  async function startNewSession() {
    const result = await createSession(botId);
    markSessionStateDirty();
    setReasoningOpenById({});
    setToolOpenById({});
    clearTodoState();
    setPendingQuestion(null);
    setPendingAnswerDraft("");
    setPendingCustomOpen(false);
    setMessages([]);
    setVisibleHistoryStartIndex(0);
    setSessionId(result.session_id);
    setMobileSessionsOpen(false);
    await refreshSessions(botId);
  }

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    markSessionStateDirty();
    setSessionId(null);
    setMessages([]);
    setVisibleHistoryStartIndex(0);
    setContextBySession({});
    setStatus(null);
    setReasoningEffort("");
    setMessageActionId(null);
    setReasoningOpenById({});
    setToolOpenById({});
    clearTodoState();
    setPendingQuestion(null);
    setPendingAnswerDraft("");
    setPendingCustomOpen(false);
    setMobileSessionsOpen(false);
    Promise.all([refreshSessions(botId), refreshBotState(botId)]).catch((err) => setStatus(errorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  useEffect(() => {
    if (!sessionId) return;
    selectSession(sessionId).catch((err) => setStatus(errorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      setDeleteOpen(false);
      setPendingDeleteSessionId(null);
    }
    if (!deleteOpen) return;
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteOpen]);

  function applyStreamEvent(ev: StreamEvent) {
    if (ev.type === "todo_snapshot") {
      setTodoSnapshot(normalizeTodoSnapshot(ev.todo));
      return;
    }

    if (ev.type === "assistant_start") {
      markSessionStateDirty();
      setMessages((prev) =>
        updateAssistantMessage(
          prev,
          ev.message_id,
          (current) => ({
            ...current,
            message_id: ev.message_id ?? current.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? current.parent_user_message_id ?? null,
          }),
          () => ({
            role: "assistant",
            content: "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            reasoning: "",
          }),
        ),
      );
      return;
    }

    if (ev.type === "assistant_reasoning_delta") {
      markSessionStateDirty();
      setMessages((prev) =>
        updateAssistantMessage(
          prev,
          ev.message_id,
          (current) => ({
            ...current,
            message_id: ev.message_id ?? current.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? current.parent_user_message_id ?? null,
            reasoning: `${current.reasoning ?? ""}${ev.reasoning_text ?? ""}`,
          }),
          () => ({
            role: "assistant",
            content: "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            reasoning: ev.reasoning_text ?? "",
          }),
        ),
      );
      return;
    }

    if (ev.type === "assistant_delta") {
      markSessionStateDirty();
      setMessages((prev) =>
        updateAssistantMessage(
          prev,
          ev.message_id,
          (current) => ({
            ...current,
            message_id: ev.message_id ?? current.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? current.parent_user_message_id ?? null,
            content: `${current.content ?? ""}${ev.delta_text ?? ""}`,
          }),
          () => ({
            role: "assistant",
            content: ev.delta_text ?? "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            reasoning: "",
          }),
        ),
      );
      return;
    }

    if (ev.type === "assistant_end") {
      markSessionStateDirty();
      setMessages((prev) =>
        updateAssistantMessage(
          prev,
          ev.message_id,
          (current) => ({
            ...current,
            message_id: ev.message_id ?? current.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? current.parent_user_message_id ?? null,
            content: typeof ev.content === "string" ? ev.content : current.content,
            reasoning: typeof ev.reasoning === "string" ? ev.reasoning : current.reasoning,
            usage: ev.usage ?? current.usage ?? null,
            tool_calls: ev.tool_calls ? normalizeStreamToolCalls(ev.tool_calls) : current.tool_calls ?? [],
          }),
          () => ({
            role: "assistant",
            content: ev.content ?? "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            reasoning: ev.reasoning ?? "",
            usage: ev.usage ?? null,
            tool_calls: normalizeStreamToolCalls(ev.tool_calls),
          }),
        ),
      );
      return;
    }

    if (ev.type === "ask_user_question") {
      const nextQuestion = derivePendingQuestionFromPrompt({
        question_id: ev.question_id ?? "",
        message_id: ev.message_id ?? null,
        prompt: ev.prompt ?? "",
        options: ev.options ?? [],
        allow_free_text: Boolean(ev.allow_free_text),
        required: Boolean(ev.required),
      });
      markSessionStateDirty();
      setPendingQuestion(nextQuestion);
      setPendingAnswerDraft("");
      setPendingCustomOpen(false);
      setMessages((prev) =>
        updateAssistantMessage(
          prev,
          ev.message_id,
          (current) => ({
            ...current,
            message_id: ev.message_id ?? current.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? current.parent_user_message_id ?? null,
            content: current.content?.trim() ? current.content : nextQuestion?.prompt ?? ev.prompt ?? current.content,
          }),
          () => ({
            role: "assistant",
            content: nextQuestion?.prompt ?? ev.prompt ?? "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            reasoning: "",
          }),
        ),
      );
      return;
    }

    if (ev.type === "ask_user_answer_received") {
      markSessionStateDirty();
      setPendingQuestion(null);
      setPendingAnswerDraft("");
      setPendingCustomOpen(false);
      setMessages((prev) => [
        ...prev,
        {
          role: "user",
          content: ev.answer_text ?? "",
        },
      ]);
      return;
    }

    if (ev.type === "tool_call") {
      return;
    }

    if (ev.type === "tool_result") {
      markSessionStateDirty();
      setMessages((prev) => [
        ...prev,
        {
          role: "tool",
          content: ev.tool_output ?? "",
          tool_call_id: ev.tool_call_id,
          tool_name: ev.tool_name ?? null,
          is_error: ev.is_error ?? null,
        },
      ]);
      return;
    }

    if (ev.type === "system" && typeof ev.message === "string" && ev.message !== "session_created") {
      const targetSessionId = ev.session_id ?? sessionId;
      const usage = normalizeUsage(ev.data?.usage);
      const contextUsage = normalizeUsage(ev.data?.context_usage);
      const contextTokens = contextUsage?.total_tokens;
      const usageTokens = usage?.total_tokens;
      if (targetSessionId && contextTokens != null) {
        setContextBySession((prev) => ({
          ...prev,
          [targetSessionId]: {
            totalTokens: contextTokens,
            compacted: Boolean(ev.data?.context_compacted),
          },
        }));
      } else if (targetSessionId && usageTokens != null) {
        setContextBySession((prev) => ({
          ...prev,
          [targetSessionId]: {
            totalTokens: usageTokens,
            compacted: false,
          },
        }));
      }
      setStatus(ev.message);
    }
  }

  function renderPendingQuestionCard(question: PendingQuestion) {
    const hasPresetOptions = question.options.length > 0;
    const showCustomComposer = question.allow_free_text && (!hasPresetOptions || pendingCustomOpen);

    return (
      <div className="rounded-[26px] border border-zinc-800 bg-[linear-gradient(180deg,rgba(22,22,22,0.98),rgba(12,12,12,0.98))] shadow-[0_14px_42px_rgba(0,0,0,0.24)]">
        <div className="px-4 py-4 sm:px-5">
          <div className="min-w-0">
            <div className="inline-flex items-center gap-2 rounded-full border border-amber-200/10 bg-amber-100/5 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-amber-100/75">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-300 shadow-[0_0_10px_rgba(252,211,77,0.45)]" />
              {t("chat.askUser.title")}
            </div>
            <div className="mt-3 max-w-2xl text-sm font-semibold leading-6 text-zinc-50">{question.prompt}</div>
          </div>
          {hasPresetOptions ? (
            <div className="mt-4 grid gap-2 lg:grid-cols-2">
              {question.options.map((option, index) => (
                <button
                  key={option.value}
                  type="button"
                  className="group rounded-[18px] border border-zinc-800 bg-zinc-950 px-3.5 py-3 text-left transition hover:border-amber-200/20 hover:bg-zinc-900 disabled:opacity-50"
                  onClick={() => void onSubmitPendingAnswer(option)}
                  disabled={submittingAnswer}
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-amber-200/12 bg-amber-100/5 text-[10px] font-semibold uppercase tracking-[0.14em] text-amber-100/70">
                      {(index + 1).toString().padStart(2, "0")}
                    </div>
                    <div className="min-w-0 flex-1 text-sm font-medium leading-6 text-zinc-100">{option.label}</div>
                  </div>
                </button>
              ))}
            </div>
          ) : null}
          {question.allow_free_text && hasPresetOptions && !pendingCustomOpen ? (
            <div className="mt-3 flex items-center">
              <button
                type="button"
                className="rounded-full border border-dashed border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-300 transition hover:border-amber-200/25 hover:text-zinc-100 disabled:opacity-50"
                onClick={() => setPendingCustomOpen(true)}
                disabled={submittingAnswer}
              >
                {t("chat.askUser.custom")}
              </button>
            </div>
          ) : null}
          {showCustomComposer ? (
            <div className={hasPresetOptions ? "mt-3 rounded-[20px] border border-zinc-800 bg-zinc-950/90 p-3" : "mt-4 rounded-[20px] border border-zinc-800 bg-zinc-950/90 p-3"}>
              <div className="flex flex-col gap-2.5 sm:flex-row sm:items-end">
                <textarea
                  value={pendingAnswerDraft}
                  onChange={(e) => setPendingAnswerDraft(e.target.value)}
                  placeholder={t("chat.askUser.customPlaceholder")}
                  className="min-h-[72px] w-full resize-y rounded-[18px] border border-zinc-800 bg-zinc-950 px-3.5 py-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-amber-200/15 disabled:opacity-60"
                  disabled={submittingAnswer}
                />
                <div className="flex gap-2">
                  {hasPresetOptions ? (
                    <button
                      type="button"
                      className="rounded-[18px] border border-zinc-800 bg-zinc-950 px-3.5 py-2.5 text-sm font-semibold text-zinc-200 hover:bg-zinc-900 disabled:opacity-50"
                      onClick={() => {
                        setPendingCustomOpen(false);
                        setPendingAnswerDraft("");
                      }}
                      disabled={submittingAnswer}
                    >
                      {t("chat.askUser.cancel")}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="rounded-[18px] bg-amber-200 px-3.5 py-2.5 text-sm font-semibold text-zinc-950 hover:bg-amber-100 disabled:opacity-50"
                    onClick={() => void onSubmitPendingAnswer()}
                    disabled={submittingAnswer || !pendingAnswerDraft.trim()}
                  >
                    {t("chat.askUser.submit")}
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  async function onSend() {
    if (!prompt.trim() || chatBusy || chatBlocked || awaitingUserAnswer) return;
    setStatus(null);
    setStreaming(true);
    clearTodoState();

    const nextPrompt = prompt.trim();
    const sid = await ensureSession();
    markSessionStateDirty();
    setMessages((prev) => [...prev, { role: "user", content: nextPrompt }]);
    setPrompt("");

    try {
      for await (const ev of streamChat(botId, sid, nextPrompt, reasoningEffort || null)) {
        applyStreamEvent(ev);
      }
      await refreshSessions(botId);
      await selectSession(sid, { force: true });
      await refreshBotState(botId);
    } catch (err) {
      setStatus(errorMessage(err));
      await refreshBotState(botId).catch(() => null);
    } finally {
      setStreaming(false);
    }
  }

  async function onSubmitPendingAnswer(option?: { label: string; value: string }) {
    if (!sessionId || !pendingQuestion || submittingAnswer) return;
    const answerText = option ? option.label : pendingAnswerDraft.trim();
    if (!answerText) return;
    setSubmittingAnswer(true);
    setStatus(null);
    try {
      await submitPendingQuestionAnswer(botId, sessionId, {
        question_id: pendingQuestion.question_id,
        answer_text: answerText,
        selected_option_value: option?.value ?? null,
      });
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setSubmittingAnswer(false);
    }
  }

  async function onStop() {
    if (!sessionId) return;
    try {
      await cancelSession(botId, sessionId);
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }

  async function onCopyMessage(message: Message) {
    if (!message.content?.trim()) return;
    try {
      await copyText(message.content);
      setStatus(t("chat.status.copiedReply"));
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }

  async function onDeleteMessage(messageId: string) {
    if (!sessionId || chatBusy) return;
    setMessageActionId(messageId);
    setStatus(null);
    try {
      const result = await deleteSessionMessage(botId, sessionId, messageId);
      setReasoningOpenById({});
      setToolOpenById({});
      setMessages(result.messages);
      setVisibleHistoryStartIndex(findHistoryWindowStart(result.messages, INITIAL_VISIBLE_HISTORY_TURNS));
      syncContextSnapshot(sessionId, result.messages);
      await refreshSessions(botId);
      setStatus(t("chat.status.deletedTurn"));
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setMessageActionId(null);
    }
  }

  async function onRegenerateMessage(messageId: string) {
    if (!sessionId || chatBusy) return;
    const previousMessages = messages;
    const optimisticMessages = removeAssistantTurn(previousMessages, messageId, true);
    setMessageActionId(messageId);
    setStatus(t("chat.status.regeneratingReply"));
    setStreaming(true);
    clearTodoState();
    try {
      let interrupted = false;
      markSessionStateDirty();
      setReasoningOpenById({});
      setToolOpenById({});
      setMessages(optimisticMessages);
      setVisibleHistoryStartIndex(findHistoryWindowStart(optimisticMessages, INITIAL_VISIBLE_HISTORY_TURNS));
      syncContextSnapshot(sessionId, optimisticMessages);
      for await (const ev of streamRegenerateSessionMessage(botId, sessionId, messageId, reasoningEffort || null)) {
        if (ev.type === "system" && ev.message === "Generation interrupted.") {
          interrupted = true;
        }
        applyStreamEvent(ev);
      }
      await refreshSessions(botId);
      await selectSession(sessionId, { force: true });
      await refreshBotState(botId);
      if (!interrupted) {
        setStatus(t("chat.status.regeneratedReply"));
      }
    } catch (err) {
      setStatus(errorMessage(err));
      try {
        await selectSession(sessionId, { force: true });
      } catch {
        markSessionStateDirty();
        setMessages(previousMessages);
        setVisibleHistoryStartIndex(findHistoryWindowStart(previousMessages, INITIAL_VISIBLE_HISTORY_TURNS));
        syncContextSnapshot(sessionId, previousMessages);
      }
      await refreshSessions(botId).catch(() => null);
      await refreshBotState(botId).catch(() => null);
    } finally {
      setStreaming(false);
      setMessageActionId(null);
    }
  }

  function requestDeleteSession(id: string) {
    if (chatBusy) return;
    setPendingDeleteSessionId(id);
    setDeleteOpen(true);
  }

  async function confirmDeleteSession() {
    const id = pendingDeleteSessionId;
    if (!id || chatBusy) return;
    try {
      setStatus(null);
      await deleteSession(botId, id);
      if (sessionId === id) {
        setSessionId(null);
        setReasoningOpenById({});
        setToolOpenById({});
        clearTodoState();
        setMessages([]);
        setVisibleHistoryStartIndex(0);
      }
      setContextBySession((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      await refreshSessions(botId);
      setDeleteOpen(false);
      setPendingDeleteSessionId(null);
    } catch (err) {
      setStatus(errorMessage(err));
    }
  }

  async function onSelectModel(nextModelId: string) {
    if (chatBusy) return;
    setModelSaving(true);
    setStatus(null);
    try {
      await updateBotConfig(botId, { chat_model_id: nextModelId || null });
      await refreshBotState(botId);
      setStatus(nextModelId ? t("chat.status.chatModelUpdated") : t("chat.status.usingFallbackModel"));
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setModelSaving(false);
    }
  }

  function toggleReasoning(messageId: string) {
    setReasoningOpenById((prev) => ({ ...prev, [messageId]: !prev[messageId] }));
  }

  function toggleToolInvocation(toolKey: string) {
    setToolOpenById((prev) => ({ ...prev, [toolKey]: !prev[toolKey] }));
  }

  function onLoadOlderMessages() {
    setVisibleHistoryStartIndex((current) =>
      expandHistoryWindowStart(messages, current, HISTORY_TURN_BATCH),
    );
  }

  const sessionListContent = (
    <>
      {sortedSessions.map((item, index) => {
        const miniLabel = item.session_id.slice(0, 2).toUpperCase();
        return (
          <div key={item.session_id} className="mb-2 flex items-stretch gap-2">
            <button
              type="button"
              className={classNames(
                "flex-1 rounded-2xl px-3 py-3 text-left transition",
                sessionId === item.session_id
                  ? "bg-zinc-900 text-zinc-100 ring-1 ring-sky-400/30"
                  : "bg-zinc-950 text-zinc-300 hover:bg-zinc-900/70",
              )}
              onClick={() => {
                setMobileSessionsOpen(false);
                setSessionId(item.session_id);
              }}
              disabled={chatBusy}
              title={item.session_id}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <div className="flex h-7 w-7 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-950 text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-400">
                    {miniLabel}
                  </div>
                  <div className="font-mono text-xs">{item.session_id}</div>
                </div>
                <div className="text-[10px] text-zinc-500">{item.message_count}</div>
              </div>
              <div className="mt-2 line-clamp-2 text-xs text-zinc-400">
                {item.preview || t("chat.sessions.noPreview")}
              </div>
              <div className="mt-3 text-[10px] uppercase tracking-[0.18em] text-zinc-600">
                #{String(index + 1).padStart(2, "0")}
              </div>
            </button>
            <button
              type="button"
              className="rounded-2xl bg-zinc-900 px-3 py-3 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
              onClick={() => requestDeleteSession(item.session_id)}
              disabled={chatBusy}
              title={t("chat.sessions.deleteTitle")}
            >
              {t("chat.sessions.deleteShort")}
            </button>
          </div>
        );
      })}
      {sortedSessions.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-6 text-sm text-zinc-500">
          {t("chat.sessions.empty")}
        </div>
      ) : null}
    </>
  );

  const todoDockContent = todoVisible && todoSnapshot ? (
    <div className="flex h-full flex-col p-4">
      <div
        className={classNames(
          "flex h-full flex-col overflow-hidden rounded-[2rem] border px-4 py-4 shadow-[0_20px_60px_rgba(0,0,0,0.24)]",
          todoDone
            ? "border-emerald-500/25 bg-[radial-gradient(circle_at_top,#153829_0%,#0a0f0d_58%,#09090b_100%)]"
            : "border-sky-500/20 bg-[radial-gradient(circle_at_top,#132136_0%,#0c1220_52%,#09090b_100%)]",
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-sky-200/75">
              {t("chat.todo.dockLabel")}
            </div>
            <div className="mt-3 text-xl font-semibold text-zinc-50">
              {todoDone ? t("chat.todo.doneTitle") : todoSnapshot.title}
            </div>
            <div className="mt-2 text-xs text-zinc-400">
              {todoDone
                ? todoCompletedAtLabel
                  ? `${t("chat.todo.completedState")} • ${todoCompletedAtLabel}`
                  : t("chat.todo.completedState")
                : activeTodoItem?.detail ?? t("chat.todo.idle")}
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-right">
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">{t("common.current")}</div>
            <div className="mt-1 text-lg font-semibold text-zinc-100">
              {todoSnapshot.completed} / {todoSnapshot.total}
            </div>
          </div>
        </div>

        <div className="mt-5 h-2 overflow-hidden rounded-full bg-zinc-900/90">
          <div
            className={classNames(
              "h-full rounded-full transition-[width,background-color] duration-300",
              todoDone ? "bg-emerald-400" : "bg-[linear-gradient(90deg,#38bdf8,#34d399)]",
            )}
            style={{ width: `${todoPercent}%` }}
          />
        </div>

        <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-500">
          <span>{todoDone ? t("chat.todo.completedState") : t("chat.todo.activeState")}</span>
          <span>{todoPercent}%</span>
        </div>

        <div className="mt-5 flex-1 overflow-auto pr-1">
          <div className="space-y-3">
            {todoSnapshot.items.map((item) => {
              const isActive = item.status === "active";
              const isDone = item.status === "done";
              const statusLabel = isDone
                ? t("chat.todo.completedState")
                : isActive
                  ? t("chat.todo.activeState")
                  : t("chat.todo.pendingState");

              return (
                <div
                  key={item.id}
                  className={classNames(
                    "relative overflow-hidden rounded-[1.4rem] border px-4 py-3 transition-all duration-300",
                    isDone
                      ? "border-emerald-500/15 bg-emerald-500/8 text-zinc-300"
                      : isActive
                        ? "border-sky-400/35 bg-sky-500/10 text-zinc-100 shadow-[0_14px_30px_rgba(14,165,233,0.12)]"
                        : "border-zinc-800 bg-zinc-950/65 text-zinc-400",
                  )}
                >
                  {isActive ? <div className="absolute inset-y-3 left-0 w-1 rounded-full bg-sky-300 animate-pulse" /> : null}
                  <div className="flex items-start gap-3">
                    <div
                      className={classNames(
                        "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border text-sm font-semibold",
                        isDone
                          ? "border-emerald-400/20 bg-emerald-500/12 text-emerald-200"
                          : isActive
                            ? "border-sky-300/20 bg-sky-400/12 text-sky-100"
                            : "border-zinc-800 bg-zinc-900 text-zinc-500",
                      )}
                    >
                      {isDone ? "✓" : isActive ? "•" : "○"}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 text-sm font-semibold leading-6">{item.label}</div>
                        <div
                          className={classNames(
                            "shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
                            isDone
                              ? "bg-emerald-500/12 text-emerald-200"
                              : isActive
                                ? "bg-sky-400/12 text-sky-100"
                                : "bg-zinc-900 text-zinc-500",
                          )}
                        >
                          {statusLabel}
                        </div>
                      </div>
                      {item.detail ? <div className="mt-1 text-xs text-zinc-400">{item.detail}</div> : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <div className="flex h-full overflow-hidden bg-zinc-950">
      <aside
        className={classNames(
          "hidden shrink-0 border-r border-zinc-800 bg-zinc-950/95 transition-[width] duration-300 lg:flex",
          sidebarCollapsed ? "w-20" : "w-80",
        )}
      >
        {sidebarCollapsed ? (
          <div className="flex h-full w-full flex-col items-center gap-4 px-3 py-4">
            <button
              type="button"
              className="flex h-12 w-12 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900 text-lg text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
              onClick={() => setSidebarCollapsed(false)}
              title={t("chat.sessions.expand")}
            >
              &gt;
            </button>
            <button
              type="button"
              className="flex h-12 w-12 items-center justify-center rounded-2xl bg-zinc-100 text-xl font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
              onClick={() => void startNewSession()}
              disabled={chatBusy || chatBlocked}
              title={t("chat.sessions.new")}
            >
              +
            </button>
            <div className="h-px w-10 bg-zinc-800" />
            <div className="flex flex-1 flex-col items-center gap-2 overflow-auto pb-2">
              {sortedSessions.map((item) => (
                <button
                  key={item.session_id}
                  type="button"
                  className={classNames(
                    "flex h-11 w-11 items-center justify-center rounded-2xl border text-[10px] font-semibold uppercase tracking-[0.16em] transition",
                    sessionId === item.session_id
                      ? "border-sky-400/30 bg-sky-400/12 text-sky-100"
                      : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200",
                  )}
                  onClick={() => setSessionId(item.session_id)}
                  disabled={chatBusy}
                  title={item.session_id}
                >
                  {item.session_id.slice(0, 2)}
                </button>
              ))}
            </div>
            <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              {sortedSessions.length}
            </div>
          </div>
        ) : (
          <div className="flex h-full flex-col">
            <div className="border-b border-zinc-800 px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{t("chat.sessions.title")}</div>
                  <div className="mt-2 text-[11px] uppercase tracking-[0.18em] text-zinc-600">
                    {sortedSessions.length}
                  </div>
                </div>
                <button
                  type="button"
                  className="flex h-10 w-10 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900 text-lg text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
                  onClick={() => setSidebarCollapsed(true)}
                  title={t("chat.sessions.collapse")}
                >
                  &lt;
                </button>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  className="rounded-2xl bg-zinc-100 px-3 py-2 text-xs font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                  onClick={() => void startNewSession()}
                  disabled={chatBusy || chatBlocked}
                >
                  {t("chat.sessions.new")}
                </button>
                <button
                  type="button"
                  className="rounded-2xl bg-zinc-900 px-3 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                  onClick={() => void refreshSessions(botId).catch((err) => setStatus(errorMessage(err)))}
                  disabled={chatBusy}
                >
                  {t("chat.sessions.refresh")}
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-auto px-3 py-3">{sessionListContent}</div>
          </div>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-zinc-800 bg-zinc-950/95 px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="mb-3 flex flex-wrap items-center gap-2 lg:hidden">
                <button
                  type="button"
                  className="rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
                  onClick={() => setMobileSessionsOpen(true)}
                >
                  {t("chat.sessions.mobileOpen")}
                </button>
                {todoVisible ? (
                  <button
                    type="button"
                    className="rounded-full border border-sky-500/20 bg-sky-500/10 px-3 py-1.5 text-xs font-semibold text-sky-100 hover:border-sky-400/30 hover:bg-sky-500/15"
                    onClick={() => setMobileTodoOpen(true)}
                  >
                    {t("chat.todo.openMobile")} {todoSnapshot?.completed} / {todoSnapshot?.total}
                  </button>
                ) : null}
              </div>
              <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{t("chat.header.label")}</div>
              <div className="mt-2 min-w-0 text-lg font-semibold text-zinc-100">
                <span className="truncate">{botName ?? botId}</span>
                {!botConfig?.enabled ? <span className="ml-2 text-sm text-amber-300">{t("chat.header.disabled")}</span> : null}
              </div>
              <div className="mt-1 text-xs text-zinc-500">
                {t("chat.header.session")} <span className="font-mono">{sessionId ?? t("chat.header.none")}</span>
              </div>
            </div>

            <div className="min-w-[220px] flex-1 self-center px-2">
              <div className="flex items-center justify-between gap-3 text-[11px] text-zinc-500">
                <span className="truncate font-semibold uppercase tracking-[0.18em]">{t("chat.context.label")}</span>
                <span className="shrink-0">
                  {contextPercent != null ? t("chat.context.percent", { percent: contextPercent }) : "--"}
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className={classNames(
                    "h-full rounded-full transition-[width,background-color] duration-300",
                    contextTone === "compacted"
                      ? "bg-emerald-400"
                      : contextTone === "danger"
                        ? "bg-rose-400"
                        : contextTone === "warning"
                          ? "bg-amber-400"
                          : "bg-sky-400",
                  )}
                  style={{ width: `${contextPercent ?? 0}%` }}
                />
              </div>
              <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-zinc-500">
                <span className="truncate">{t(contextStatusKey)}</span>
                <span className="shrink-0">
                  {activeContextSnapshot
                    ? new Intl.NumberFormat(sessionLocale).format(activeContextSnapshot.totalTokens)
                    : "--"}
                  {contextThresholdTokens
                    ? ` / ${new Intl.NumberFormat(sessionLocale).format(contextThresholdTokens)}`
                    : ""}
                </span>
              </div>
            </div>

            <div className="flex min-w-[320px] flex-col gap-2">
              <div
                className={classNames(
                  "grid gap-1",
                  streaming ? "sm:grid-cols-[minmax(0,1fr)_180px_auto]" : "sm:grid-cols-[minmax(0,1fr)_180px]",
                )}
              >
                <label className="text-xs font-semibold uppercase tracking-[0.22em] text-zinc-500">
                  {t("chat.model.label")}
                </label>
                <label className="text-xs font-semibold uppercase tracking-[0.22em] text-zinc-500">
                  {t("chat.reasoningEffort.label")}
                </label>
                {streaming ? <span aria-hidden="true" /> : null}
              </div>
              <div
                className={classNames(
                  "grid gap-2",
                  streaming ? "sm:grid-cols-[minmax(0,1fr)_180px_auto]" : "sm:grid-cols-[minmax(0,1fr)_180px]",
                )}
              >
                <select
                  value={botConfig?.chat_model_id ?? ""}
                  onChange={(e) => void onSelectModel(e.target.value)}
                  className="w-full rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                  disabled={chatBusy || modelSaving}
                >
                  <option value="">{t("chat.model.fallbackOption")}</option>
                  {availableModels.map((item) => (
                    <option key={item.model_id} value={item.model_id}>
                      {item.provider_name} / {item.label}
                    </option>
                  ))}
                </select>
                <select
                  value={reasoningEffort}
                  onChange={(e) => setReasoningEffort(e.target.value as ReasoningEffort | "")}
                  className="w-full rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                  disabled={chatBusy}
                >
                  <option value="">{t("chat.reasoningEffort.auto")}</option>
                  <option value="low">{t("chat.reasoningEffort.low")}</option>
                  <option value="medium">{t("chat.reasoningEffort.medium")}</option>
                  <option value="high">{t("chat.reasoningEffort.high")}</option>
                </select>
                {streaming ? (
                  <button
                    type="button"
                    className="rounded-2xl bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500"
                    onClick={() => void onStop()}
                  >
                    {t("chat.model.stop")}
                  </button>
                ) : null}
              </div>
              <div className="text-xs text-zinc-500">
                {selectedModel
                  ? t("chat.model.usingSelected", {
                      provider: selectedModel.provider_name,
                      label: selectedModel.label,
                    })
                  : t("chat.model.usingFallback", { model: botConfig?.model ?? "" })}
              </div>
              {reasoningEffort ? (
                <div className="text-xs text-zinc-500">
                  {t("chat.reasoningEffort.current", {
                    effort: t(`chat.reasoningEffort.${reasoningEffort}`),
                  })}
                </div>
              ) : null}
            </div>
          </div>

          {blockedReason ? (
            <div className="mt-4 rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              {blockedReason}
            </div>
          ) : null}

          {status ? <div className="mt-3 text-xs text-zinc-400">{status}</div> : null}
        </div>

        <div ref={scrollRef} className="flex-1 overflow-auto px-4 py-6 sm:px-6">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {hiddenHistoryTurnCount > 0 ? (
              <div className="flex justify-center">
                <button
                  type="button"
                  className="rounded-full border border-zinc-800 bg-zinc-900/80 px-4 py-2 text-xs font-semibold text-zinc-300 transition hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-100"
                  onClick={onLoadOlderMessages}
                >
                  {t("chat.history.loadOlder", { count: hiddenHistoryTurnCount })}
                </button>
              </div>
            ) : null}

            {visibleMessageEntries.map(({ message, index: messageIndex }) => {
              if (message.role === "assistant") {
                const assistantInvocations = toolRenderState.assistantInvocationsByIndex.get(messageIndex) ?? [];
                const usage = usageParts(message.usage);
                const messageId = message.message_id ?? null;
                const inlinePendingQuestion =
                  pendingQuestion && messageId && pendingQuestion.message_id === messageId ? pendingQuestion : null;
                const showAssistantCard = hasVisibleAssistantContent(message) || Boolean(usage);
                if (!showAssistantCard && assistantInvocations.length === 0 && !inlinePendingQuestion) {
                  return null;
                }

                const reasoningText = message.reasoning?.trim() ?? "";
                const showReasoning = Boolean(reasoningText && message.message_id);
                const actionBusy = Boolean(messageId && messageActionId === messageId);
                const canRegenerate = Boolean(
                  messageId && latestRegeneratableAssistantId === messageId && !chatBusy,
                );
                const canDelete = Boolean(messageId && !chatBusy);
                const reasoningOpen = Boolean(messageId && reasoningOpenById[messageId]);
                const assistantText = message.content?.trim() ?? "";
                const wideAssistantShell = showReasoning || assistantInvocations.length > 0;

                return (
                  <div key={message.message_id ?? `assistant-${messageIndex}`} className="flex w-full justify-start">
                    <div
                      className={classNames(
                        "flex max-w-[85%] min-w-0 flex-col gap-3",
                        wideAssistantShell && "w-full",
                      )}
                    >
                      {showAssistantCard ? (
                        <div
                          className={classNames(
                            "rounded-3xl bg-zinc-900 px-4 py-3 text-sm leading-relaxed text-zinc-100 shadow-[0_12px_40px_rgba(0,0,0,0.18)]",
                            wideAssistantShell && "w-full",
                          )}
                        >
                          {message.is_compaction ? (
                            <div className="mb-3 inline-flex rounded-full border border-emerald-400/20 bg-emerald-400/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-200">
                              {t("chat.context.compacted")}
                            </div>
                          ) : null}
                          {showReasoning && messageId ? (
                            <div className="mb-3 rounded-2xl border border-zinc-800 bg-zinc-950/70">
                              <button
                                type="button"
                                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.18em] text-zinc-400 hover:text-zinc-200"
                                onClick={() => toggleReasoning(messageId)}
                              >
                                <span>{t("chat.reasoning.title")}</span>
                                <span className="text-sm text-zinc-500">{reasoningOpen ? "▾" : "▸"}</span>
                              </button>
                              {reasoningOpen ? (
                                <div className="border-t border-zinc-800 px-4 py-3 text-sm text-zinc-300">
                                  <pre className="whitespace-pre-wrap font-sans leading-relaxed">{reasoningText}</pre>
                                </div>
                              ) : null}
                            </div>
                          ) : null}

                          {assistantText ? (
                            <div className="markdown-content">
                              <ReactMarkdown
                                remarkPlugins={[remarkGfm]}
                                components={{
                                  table({ node, ...props }) {
                                    const columnCount = getMarkdownTableColumnCount(node);
                                    const isWideTable = columnCount >= 4;
                                    return (
                                      <div
                                        className="markdown-table-wrap"
                                        data-wide={isWideTable ? "true" : undefined}
                                        style={markdownWideTableStyle(columnCount)}
                                      >
                                        <table {...props} />
                                      </div>
                                    );
                                  },
                                }}
                              >
                                {message.content}
                              </ReactMarkdown>
                            </div>
                          ) : null}

                          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-800/80 pt-3 text-[11px] text-zinc-400">
                            <button
                              type="button"
                              className="rounded-full border border-zinc-800 px-3 py-1 text-zinc-300 hover:border-zinc-700 hover:text-zinc-100 disabled:opacity-40"
                              onClick={() => void onCopyMessage(message)}
                              disabled={actionBusy || !message.content?.trim()}
                            >
                              {t("chat.message.copy")}
                            </button>
                            {canRegenerate ? (
                                <button
                                  type="button"
                                  className="rounded-full border border-zinc-800 px-3 py-1 text-zinc-300 hover:border-zinc-700 hover:text-zinc-100 disabled:opacity-40"
                                  onClick={() => {
                                    if (messageId) void onRegenerateMessage(messageId);
                                  }}
                                  disabled={actionBusy}
                                >
                                  {t("chat.message.regenerate")}
                                </button>
                            ) : null}
                            {canDelete ? (
                                <button
                                  type="button"
                                  className="rounded-full border border-zinc-800 px-3 py-1 text-zinc-300 hover:border-red-500/40 hover:text-red-200 disabled:opacity-40"
                                  onClick={() => {
                                    if (messageId) void onDeleteMessage(messageId);
                                  }}
                                  disabled={actionBusy}
                                >
                                  {t("chat.message.delete")}
                                </button>
                            ) : null}
                            {usage ? (
                              <div className="ml-auto flex flex-wrap items-center gap-2">
                                {usage.total != null ? (
                                  <span className="rounded-full bg-zinc-950 px-3 py-1 text-zinc-300">
                                    {t("chat.message.tokens", { count: usage.total })}
                                  </span>
                                ) : null}
                                {usage.prompt != null ? (
                                  <span className="rounded-full bg-zinc-950 px-3 py-1 text-zinc-400">↑ {usage.prompt}</span>
                                ) : null}
                                {usage.completion != null ? (
                                  <span className="rounded-full bg-zinc-950 px-3 py-1 text-zinc-400">↓ {usage.completion}</span>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      ) : null}

                      {inlinePendingQuestion ? renderPendingQuestionCard(inlinePendingQuestion) : null}

                      {assistantInvocations.map((invocation) => (
                        <ToolInvocationPanel
                          key={invocation.key}
                          invocation={invocation}
                          open={Boolean(toolOpenById[invocation.key])}
                          onToggle={() => toggleToolInvocation(invocation.key)}
                        />
                      ))}
                    </div>
                  </div>
                );
              }

              if (message.role === "tool") {
                if (toolRenderState.linkedToolMessageIndexes.has(messageIndex)) {
                  return null;
                }

                const invocation = toolRenderState.orphanInvocationsByIndex.get(messageIndex);
                if (!invocation) {
                  return null;
                }

                return (
                  <div key={invocation.key} className="flex w-full justify-start">
                    <div className="w-full max-w-[85%] min-w-0">
                      <ToolInvocationPanel
                        invocation={invocation}
                        open={Boolean(toolOpenById[invocation.key])}
                        onToggle={() => toggleToolInvocation(invocation.key)}
                      />
                    </div>
                  </div>
                );
              }

              return (
                <div key={message.message_id ?? `user-${messageIndex}`} className="flex justify-end">
                  <div className="max-w-[85%] rounded-3xl bg-zinc-100 px-4 py-3 text-sm leading-relaxed text-zinc-900 shadow-[0_12px_40px_rgba(0,0,0,0.18)]">
                    <pre className="whitespace-pre-wrap font-sans">{message.content}</pre>
                  </div>
                </div>
              );
            })}

            {messages.length === 0 ? (
              <div className="rounded-3xl border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
                {chatBlocked ? t("chat.empty.blocked") : t("chat.empty.ready")}
              </div>
            ) : null}
          </div>
        </div>

        <div className="border-t border-zinc-800 bg-zinc-950/95 px-5 py-4">
          {pendingQuestion && showPendingQuestionFallback ? (
            <div className="mx-auto mb-3 max-w-3xl">{renderPendingQuestionCard(pendingQuestion)}</div>
          ) : null}

          <div className="mx-auto flex max-w-4xl items-end gap-3">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={chatBlocked ? t("chat.input.blocked") : awaitingUserAnswer ? t("chat.input.answerPending") : t("chat.input.ready")}
              className="min-h-[48px] w-full resize-none rounded-3xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600 disabled:opacity-60"
              rows={1}
              disabled={chatBusy || chatBlocked || awaitingUserAnswer}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void onSend();
                }
              }}
            />
            <button
              type="button"
              className="rounded-3xl bg-zinc-100 px-5 py-3 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
              onClick={() => void onSend()}
              disabled={chatBusy || chatBlocked || awaitingUserAnswer || !prompt.trim()}
            >
              {t("chat.input.send")}
            </button>
          </div>
          <div className="mx-auto mt-2 max-w-4xl text-xs text-zinc-500">{t("chat.input.hint")}</div>
        </div>
      </section>

      {todoDockContent ? (
        <aside className="hidden w-[320px] shrink-0 border-l border-zinc-800 bg-zinc-950/95 lg:flex">
          {todoDockContent}
        </aside>
      ) : null}

      {mobileSessionsOpen ? (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="flex h-full w-[min(24rem,calc(100vw-2.5rem))] flex-col border-r border-zinc-800 bg-zinc-950/98 shadow-2xl">
            <div className="border-b border-zinc-800 px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{t("chat.sessions.title")}</div>
                <button
                  type="button"
                  className="flex h-10 w-10 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900 text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
                  onClick={() => setMobileSessionsOpen(false)}
                >
                  ×
                </button>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  className="rounded-2xl bg-zinc-100 px-3 py-2 text-xs font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                  onClick={() => void startNewSession()}
                  disabled={chatBusy || chatBlocked}
                >
                  {t("chat.sessions.new")}
                </button>
                <button
                  type="button"
                  className="rounded-2xl bg-zinc-900 px-3 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                  onClick={() => void refreshSessions(botId).catch((err) => setStatus(errorMessage(err)))}
                  disabled={chatBusy}
                >
                  {t("chat.sessions.refresh")}
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-auto px-3 py-3">{sessionListContent}</div>
          </div>
          <button
            type="button"
            className="flex-1 bg-black/70"
            onClick={() => setMobileSessionsOpen(false)}
            aria-label={t("common.cancel")}
          />
        </div>
      ) : null}

      {mobileTodoOpen && todoDockContent ? (
        <div className="fixed inset-0 z-40 flex justify-end lg:hidden">
          <button
            type="button"
            className="flex-1 bg-black/70"
            onClick={() => setMobileTodoOpen(false)}
            aria-label={t("common.cancel")}
          />
          <div className="h-full w-[min(24rem,calc(100vw-1rem))] border-l border-zinc-800 bg-zinc-950/98 shadow-2xl">
            <div className="flex items-center justify-end px-4 pt-4">
              <button
                type="button"
                className="flex h-10 w-10 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900 text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
                onClick={() => setMobileTodoOpen(false)}
              >
                ×
              </button>
            </div>
            {todoDockContent}
          </div>
        </div>
      ) : null}

      {deleteOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) {
              setDeleteOpen(false);
              setPendingDeleteSessionId(null);
            }
          }}
        >
          <div className="w-full max-w-md rounded-3xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
            <div className="text-lg font-semibold text-zinc-100">{t("chat.deleteSession.title")}</div>
            <div className="mt-2 text-sm text-zinc-400">
              {t("chat.deleteSession.description", {
                sessionId: pendingDeleteSessionId ?? t("common.unknown"),
              })}
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800"
                onClick={() => {
                  setDeleteOpen(false);
                  setPendingDeleteSessionId(null);
                }}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="rounded-2xl bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                onClick={() => void confirmDeleteSession()}
                disabled={!pendingDeleteSessionId || chatBusy}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
