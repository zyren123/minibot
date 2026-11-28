import { useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  Activity, ArrowDownLeft, ArrowUp, ChevronDown, ChevronRight, Copy, FileText, FlaskConical,
  FolderSearch, ListChecks, PanelLeft, PanelLeftClose, PanelLeftOpen, PanelRight, RefreshCw,
  RotateCcw, Search, Square, SquarePen, Terminal, Trash2, X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  AvailableModel,
  BotConfig,
  ChatStreamEvent,
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
  submitPendingQuestionAnswerStream,
  submitToolApproval,
  streamRegenerateSessionMessage,
  streamChat,
  updateBotConfig,
} from "../lib/api";
import { useI18n } from "../lib/i18n";
import { IconButton } from "../components/IconButton";
import { GenerationIndicator } from "./GenerationIndicator";
import { ToolApprovalCard, type PendingToolApproval } from "./ToolApprovalCard";
import {
  countHiddenTurnsBeforeIndex,
  expandHistoryWindowStart,
  findHistoryWindowStart,
} from "./chatHistoryWindow";
import {
  buildToolRenderState,
  normalizeStreamToolCalls,
  type ToolInvocationView,
} from "./chatToolState";

const INITIAL_VISIBLE_HISTORY_TURNS = 24;
const HISTORY_TURN_BATCH = 24;
const DESKTOP_CHAT = "(min-width: 1024px)";
const STARTER_PROMPTS = [
  { key: "explore", icon: FolderSearch },
  { key: "diagnose", icon: FlaskConical },
  { key: "plan", icon: ListChecks },
] as const;

type InspectorPane = "activity" | "context" | "plan";
type ChatRun = { botId: string; kind: "send" | "answer" | "regenerate" };
type DeletionTarget =
  | { kind: "session"; sessionId: string }
  | { kind: "turn"; sessionId: string; messageId: string };
type DeletionRequest =
  | { target: DeletionTarget; state: "confirm" | "submitting" }
  | { target: DeletionTarget; state: "error"; message: string };

type ToolApprovalNotice = {
  botId: string;
  sessionId: string;
  text: string;
};

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

function normalizeTodoSnapshot(raw: ChatStreamEvent["todo"] | undefined): TodoSnapshot | null {
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

const chatGlyphs = {
  write: SquarePen,
  down: ChevronDown,
  right: ChevronRight,
  stop: Square,
  file: FileText,
  terminal: Terminal,
  activity: Activity,
};

function ChatGlyph({ name }: { name: keyof typeof chatGlyphs }) {
  const Glyph = chatGlyphs[name];
  return <Glyph size={16} strokeWidth={1.65} aria-hidden="true" focusable="false" />;
}

function ToolInvocationPanel({ invocation, open, onToggle }: {
  invocation: ToolInvocationView;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  return (
    <li className="chat-tool-row">
      <button type="button" className="chat-tool-disclosure" onClick={onToggle} aria-expanded={open}>
        <ChatGlyph name={/shell|bash|exec|terminal/i.test(invocation.name) ? "terminal" : "file"} />
        <span className="chat-tool-name">{invocation.name || t("chat.tool.badge")}</span>
        <span className="chat-tool-state" data-state={invocation.status}>{t(`chat.tool.status.${invocation.status}`)}</span>
        <span className="chat-tool-chevron"><ChatGlyph name="down" /></span>
      </button>
      {open ? (
        <div className="chat-tool-detail">
          <div>
            <span>{t("chat.tool.arguments")}</span>
            {invocation.argumentsText ? <pre tabIndex={0} dir="ltr">{invocation.argumentsText}</pre> : <p>{t("chat.tool.noArguments")}</p>}
          </div>
          <div>
            <span>{t("chat.tool.result")}</span>
            {invocation.outputText ? <pre tabIndex={0} dir="ltr">{invocation.outputText}</pre> : (
              <p>{t(invocation.status === "running" ? "chat.tool.waiting" : "chat.tool.noResult")}</p>
            )}
          </div>
        </div>
      ) : null}
    </li>
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
  const [chatRun, setChatRun] = useState<ChatRun | null>(null);
  const [generatingRun, setGeneratingRun] = useState<ChatRun | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [botConfig, setBotConfig] = useState<BotConfig | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort | "">("");
  const [modelSaving, setModelSaving] = useState(false);
  const [deletionRequest, setDeletionRequest] = useState<DeletionRequest | null>(null);
  const [messageActionId, setMessageActionId] = useState<string | null>(null);
  const [reasoningOpenById, setReasoningOpenById] = useState<Record<string, boolean>>({});
  const [toolOpenById, setToolOpenById] = useState<Record<string, boolean>>({});
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSessionsOpen, setMobileSessionsOpen] = useState(false);
  const [desktop, setDesktop] = useState(() => typeof window !== "undefined" && window.matchMedia(DESKTOP_CHAT).matches);
  const [desktopInspectorOpen, setDesktopInspectorOpen] = useState(true);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [inspectorPane, setInspectorPane] = useState<InspectorPane>("activity");
  const [activityMessageIndex, setActivityMessageIndex] = useState<number | null>(null);
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  const [sessionSearch, setSessionSearch] = useState("");
  const [todoSnapshot, setTodoSnapshot] = useState<TodoSnapshot | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [pendingAnswerDraft, setPendingAnswerDraft] = useState("");
  const [pendingCustomOpen, setPendingCustomOpen] = useState(false);
  const [toolApprovals, setToolApprovals] = useState<PendingToolApproval[]>([]);
  const [toolApprovalNotice, setToolApprovalNotice] = useState<ToolApprovalNotice | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const composerHintId = useId();
  const mobileSessionsDialog = useRef<HTMLDialogElement | null>(null);
  const mobileInspectorDialog = useRef<HTMLDialogElement | null>(null);
  const deleteDialog = useRef<HTMLDialogElement | null>(null);
  const deleteDialogId = useId();
  const sessionSearchRef = useRef<HTMLInputElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const botIdRef = useRef(botId);
  const chatRunRef = useRef<ChatRun | null>(null);
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
  const activeInspectorPane = inspectorPane === "plan" && !todoVisible ? "activity" : inspectorPane;
  const inspectorOpen = desktop ? desktopInspectorOpen : mobileInspectorOpen;
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
  const activityInvocations = useMemo(() => {
    if (activityMessageIndex != null) {
      const orphan = toolRenderState.orphanInvocationsByIndex.get(activityMessageIndex);
      return orphan ? [orphan] : toolRenderState.assistantInvocationsByIndex.get(activityMessageIndex) ?? [];
    }
    let latestUser = messages.length - 1;
    while (latestUser >= 0 && messages[latestUser].role !== "user") latestUser -= 1;
    const result: ToolInvocationView[] = [];
    for (let index = latestUser + 1; index < messages.length; index += 1) {
      const assistantTools = toolRenderState.assistantInvocationsByIndex.get(index);
      const orphan = toolRenderState.orphanInvocationsByIndex.get(index);
      if (assistantTools) result.push(...assistantTools);
      if (orphan) result.push(orphan);
    }
    return result;
  }, [activityMessageIndex, messages, toolRenderState]);
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

  const visibleToolApprovals = toolApprovals.filter((approval) => (
    approval.botId === botId && approval.session_id === sessionId
  ));
  const streaming = chatRun?.botId === botId;
  const submittingAnswer = streaming && chatRun.kind === "answer";
  const chatBusy = streaming;
  const deletionDialogOpen = deletionRequest !== null;
  const deleting = deletionRequest?.state === "submitting";
  const chatBlocked = Boolean(botConfig && (!botConfig.enabled || !botConfig.chat_ready));
  const blockedReason =
    botConfig?.chat_disabled_reason ?? (botConfig?.enabled === false ? t("chat.fallbackDisabledReason") : null);
  const awaitingUserAnswer = Boolean(pendingQuestion);
  const isGenerating = streaming && !awaitingUserAnswer && visibleToolApprovals.length === 0
    && generatingRun === chatRun;
  const approvalsInInspector = inspectorOpen && activeInspectorPane === "activity";
  const pendingQuestionMessageId = pendingQuestion?.message_id ?? null;
  const showPendingQuestionFallback = Boolean(
    pendingQuestion && (!pendingQuestionMessageId || !messages.some((item) => item.role === "assistant" && item.message_id === pendingQuestionMessageId)),
  );

  function clearTodoState() {
    setTodoSnapshot(null);
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
    if (botIdRef.current !== nextBotId) return;
    setSessions(list);
    setSessionId((prev) => {
      if (prev && list.some((item) => item.session_id === prev)) return prev;
      return list.length > 0 ? list[0].session_id : null;
    });
  }

  async function refreshBotState(nextBotId: string = botId) {
    const [cfg, models] = await Promise.all([getBotConfig(nextBotId), listAvailableModels()]);
    if (botIdRef.current !== nextBotId) return;
    setBotConfig(cfg);
    setAvailableModels(models);
  }

  async function ensureSession(run: ChatRun) {
    if (sessionId) return sessionId;
    const created = await createSession(run.botId);
    if (!ownsChatRun(run)) return null;
    setReasoningOpenById({});
    setToolOpenById({});
    clearTodoState();
    sessionIdRef.current = created.session_id;
    setSessionId(created.session_id);
    await refreshSessions(botId);
    return created.session_id;
  }

  async function selectSession(id: string, options?: { force?: boolean; run?: ChatRun }) {
    const expectedVersion = sessionStateVersionRef.current;
    const [sess, question] = await Promise.all([loadSession(botId, id), getPendingQuestion(botId, id)]);
    if (botIdRef.current !== botId || sessionIdRef.current !== id) return;
    if (options?.run && !ownsChatRun(options.run)) return;
    if (!options?.force && sessionStateVersionRef.current !== expectedVersion) return;
    setReasoningOpenById({});
    setToolOpenById({});
    setActivityMessageIndex(null);
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
    try {
      setStatus(null);
      const result = await createSession(botId);
      if (botIdRef.current !== botId) return;
      markSessionStateDirty();
      setReasoningOpenById({});
      setToolOpenById({});
      clearTodoState();
      setPendingQuestion(null);
      setPendingAnswerDraft("");
      setPendingCustomOpen(false);
      setMessages([]);
      setVisibleHistoryStartIndex(0);
      setActivityMessageIndex(null);
      setSessionId(result.session_id);
      setMobileSessionsOpen(false);
      await refreshSessions(botId);
    } catch (err) {
      setMobileSessionsOpen(false);
      setStatus(errorMessage(err));
    }
  }

  useEffect(() => {
    sessionIdRef.current = sessionId;
    botIdRef.current = botId;
    setToolApprovals((current) => current.filter((approval) => (
      approval.botId === botId && approval.session_id === sessionId
    )));
    setToolApprovalNotice((current) => (
      current?.botId === botId && current.sessionId === sessionId ? current : null
    ));
  }, [botId, sessionId]);

  useEffect(() => {
    markSessionStateDirty();
    setSessionId(null);
    setMessages([]);
    setVisibleHistoryStartIndex(0);
    setContextBySession({});
    chatRunRef.current = null;
    setChatRun(null);
    setGeneratingRun(null);
    setActivityMessageIndex(null);
    setMobileInspectorOpen(false);
    setInspectorPane("activity");
    setSessionSearch("");
    setSessionSearchOpen(false);
    setDeletionRequest(null);
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
  }, [messages, streaming, isGenerating]);

  useEffect(() => {
    const dialog = mobileSessionsDialog.current;
    if (!mobileSessionsOpen || !dialog) return;
    dialog.showModal();
    const wideViewport = window.matchMedia(DESKTOP_CHAT);
    function closeOnDesktop(event: MediaQueryListEvent) {
      if (event.matches) setMobileSessionsOpen(false);
    }
    wideViewport.addEventListener("change", closeOnDesktop);
    return () => {
      wideViewport.removeEventListener("change", closeOnDesktop);
      dialog.close();
    };
  }, [mobileSessionsOpen]);

  useEffect(() => {
    const dialog = deleteDialog.current;
    if (!deletionDialogOpen || !dialog) return;
    dialog.showModal();
    return () => dialog.close();
  }, [deletionDialogOpen]);

  useEffect(() => {
    const viewport = window.matchMedia(DESKTOP_CHAT);
    const onChange = (event: MediaQueryListEvent) => {
      setDesktop(event.matches);
      if (event.matches) setMobileInspectorOpen(false);
    };
    setDesktop(viewport.matches);
    viewport.addEventListener("change", onChange);
    return () => viewport.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const dialog = mobileInspectorDialog.current;
    if (!mobileInspectorOpen || desktop || !dialog) return;
    dialog.showModal();
    return () => dialog.close();
  }, [desktop, mobileInspectorOpen]);

  useEffect(() => {
    if (sessionSearchOpen) sessionSearchRef.current?.focus();
  }, [sessionSearchOpen, mobileSessionsOpen, sidebarCollapsed, desktop]);

  function openInspector(pane: InspectorPane, messageIndex: number | null = null) {
    setInspectorPane(pane);
    setActivityMessageIndex(messageIndex);
    if (desktop) setDesktopInspectorOpen(true);
    else setMobileInspectorOpen(true);
  }

  function removeToolApproval(bot: string, session: string, approvalId: string) {
    setToolApprovals((current) => current.filter((approval) => (
      approval.botId !== bot || approval.session_id !== session || approval.approval_id !== approvalId
    )));
  }

  function clearToolApprovals(bot: string, session: string) {
    setToolApprovals((current) => current.filter((approval) => (
      approval.botId !== bot || approval.session_id !== session
    )));
  }

  function applyStreamEvent(ev: StreamEvent, streamSessionId: string, run: ChatRun) {
    if (ev.type === "model_generation_start") {
      setGeneratingRun(run);
      return;
    }
    if (ev.type === "model_generation_end") {
      setGeneratingRun(null);
      return;
    }
    if (ev.type === "tool_approval_request" || ev.type === "tool_approval_resolved") {
      if (
        ev.session_id !== streamSessionId ||
        botIdRef.current !== botId || sessionIdRef.current !== streamSessionId
      ) return;

      if (ev.type === "tool_approval_request") {
        if (desktop) openInspector("activity");
        setToolApprovals((current) => current.some((approval) => approval.approval_id === ev.approval_id)
          ? current
          : [...current, { ...ev, botId, state: "pending" }]);
      } else {
        removeToolApproval(botId, streamSessionId, ev.approval_id);
        if (ev.approval_source === "auto_review" && ev.tool_name) {
          setToolApprovalNotice({
            botId,
            sessionId: streamSessionId,
            text: t(ev.approved ? "chat.approval.autoApproved" : "chat.approval.autoDenied", { tool: ev.tool_name }),
          });
        }
      }
      return;
    }

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
            completion_state: current.completion_state ?? "complete",
          }),
          () => ({
            role: "assistant",
            content: "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            completion_state: "complete",
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
            completion_state: current.completion_state ?? "complete",
            reasoning: `${current.reasoning ?? ""}${ev.reasoning_text ?? ""}`,
          }),
          () => ({
            role: "assistant",
            content: "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            completion_state: "complete",
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
            completion_state: current.completion_state ?? "complete",
            content: `${current.content ?? ""}${ev.delta_text ?? ""}`,
          }),
          () => ({
            role: "assistant",
            content: ev.delta_text ?? "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            completion_state: "complete",
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
            completion_state: "complete",
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
            completion_state: "complete",
            reasoning: ev.reasoning ?? "",
            usage: ev.usage ?? null,
            tool_calls: normalizeStreamToolCalls(ev.tool_calls),
          }),
        ),
      );
      return;
    }

    if (ev.type === "ask_user_question") {
      const usage = normalizeUsage(ev.usage);
      const contextUsage = normalizeUsage(ev.context_usage);
      const contextTokens = contextUsage?.total_tokens;
      const usageTokens = usage?.total_tokens;
      const targetSessionId = ev.session_id ?? sessionId;
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
            usage: usage ?? current.usage ?? null,
            context_usage: contextUsage ?? current.context_usage ?? null,
          }),
          () => ({
            role: "assistant",
            content: nextQuestion?.prompt ?? ev.prompt ?? "",
            message_id: ev.message_id ?? null,
            parent_user_message_id: ev.parent_user_message_id ?? null,
            reasoning: "",
            usage,
            context_usage: contextUsage,
          }),
        ),
      );
      if (targetSessionId && contextTokens != null) {
        setContextBySession((prev) => ({
          ...prev,
          [targetSessionId]: {
            totalTokens: contextTokens,
            compacted: Boolean(ev.context_compacted),
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
      return;
    }

    if (ev.type === "ask_user_answer_received") {
      markSessionStateDirty();
      setPendingQuestion(null);
      setPendingAnswerDraft("");
      setPendingCustomOpen(false);
      if (run.kind === "answer") return;
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
          tool_args: ev.tool_args ?? null,
          is_error: ev.is_error ?? null,
          tool_status: ev.tool_status,
          tool_exit_code: ev.tool_exit_code,
          tool_artifact_id: ev.tool_artifact_id,
          tool_hook_error: ev.tool_hook_error,
          tool_hook_phase: ev.tool_hook_phase,
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

  function beginChatRun(kind: ChatRun["kind"]): ChatRun {
    const run = { botId, kind };
    chatRunRef.current = run;
    setChatRun(run);
    setGeneratingRun(null);
    setActivityMessageIndex(null);
    clearTodoState();
    return run;
  }

  function ownsChatRun(run: ChatRun) {
    return chatRunRef.current === run && botIdRef.current === run.botId;
  }

  function finishChatRun(run: ChatRun) {
    if (!ownsChatRun(run)) return;
    chatRunRef.current = null;
    setChatRun(null);
    setGeneratingRun(null);
    if (run.kind === "regenerate") setMessageActionId(null);
  }

  async function consumeChatStream(events: AsyncIterable<StreamEvent>, streamSessionId: string, run: ChatRun) {
    setToolApprovalNotice(null);
    try {
      for await (const event of events) {
        if (ownsChatRun(run) && sessionIdRef.current === streamSessionId) {
          applyStreamEvent(event, streamSessionId, run);
        }
      }
    } finally {
      if (ownsChatRun(run)) {
        setGeneratingRun(null);
        clearToolApprovals(run.botId, streamSessionId);
      }
    }
  }

  async function refreshAfterRun(run: ChatRun, sid: string) {
    if (!ownsChatRun(run)) return;
    await refreshSessions(run.botId);
    if (!ownsChatRun(run)) return;
    await selectSession(sid, { force: true, run });
    if (ownsChatRun(run)) await refreshBotState(run.botId);
  }

  async function onSubmitToolApproval(approval: PendingToolApproval, approved: boolean) {
    if (approval.state === "submitting" || approval.botId !== botId || approval.session_id !== sessionId) return;
    const matches = (item: PendingToolApproval) => (
      item.botId === approval.botId &&
      item.session_id === approval.session_id &&
      item.approval_id === approval.approval_id
    );
    setToolApprovals((current) => current.map((item) => matches(item) ? { ...item, state: "submitting" } : item));
    try {
      const result = await submitToolApproval({
        botId: approval.botId,
        sessionId: approval.session_id,
        approvalId: approval.approval_id,
        approved,
      });
      removeToolApproval(approval.botId, approval.session_id, approval.approval_id);
      if (result === "stale" && botIdRef.current === approval.botId &&
          sessionIdRef.current === approval.session_id) {
        setToolApprovalNotice({
          botId: approval.botId,
          sessionId: approval.session_id,
          text: t("chat.approval.stale"),
        });
      }
    } catch {
      setToolApprovals((current) => current.map((item) => matches(item) ? { ...item, state: "error" } : item));
    }
  }

  function renderPendingQuestionCard(question: PendingQuestion) {
    const hasPresetOptions = question.options.length > 0;
    const showCustomComposer = question.allow_free_text && (!hasPresetOptions || pendingCustomOpen);

    return (
      <section className="chat-question" aria-label={t("chat.askUser.title")}>
        <h3>{question.prompt}</h3>
        {hasPresetOptions ? (
          <div className="chat-question-options">
            {question.options.map((option) => (
              <button
                key={option.value}
                type="button"
                className="chat-button"
                onClick={() => void onSubmitPendingAnswer(option)}
                disabled={submittingAnswer}
              >
                <span>{option.label}</span>
                <ChatGlyph name="right" />
              </button>
            ))}
          </div>
        ) : null}
        {question.allow_free_text && hasPresetOptions && !pendingCustomOpen ? (
          <button type="button" className="chat-button chat-button-quiet" onClick={() => setPendingCustomOpen(true)} disabled={submittingAnswer}>
            {t("chat.askUser.custom")}
          </button>
        ) : null}
        {showCustomComposer ? (
          <div className="chat-question-composer">
            <textarea
              aria-label={t("chat.askUser.customPlaceholder")}
              value={pendingAnswerDraft}
              onChange={(e) => setPendingAnswerDraft(e.target.value)}
              placeholder={t("chat.askUser.customPlaceholder")}
              disabled={submittingAnswer}
            />
            <div>
              {hasPresetOptions ? (
                <button
                  type="button"
                  className="chat-button"
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
                className="chat-button chat-button-primary"
                onClick={() => void onSubmitPendingAnswer()}
                disabled={submittingAnswer || !pendingAnswerDraft.trim()}
              >
                {t("chat.askUser.submit")}
              </button>
            </div>
          </div>
        ) : null}
      </section>
    );
  }

  async function onSend() {
    if (!prompt.trim() || chatRunRef.current || chatBlocked || awaitingUserAnswer) return;
    setStatus(null);
    const run = beginChatRun("send");
    const nextPrompt = prompt.trim();

    try {
      const sid = await ensureSession(run);
      if (!sid || !ownsChatRun(run)) return;
      markSessionStateDirty();
      setMessages((prev) => [...prev, { role: "user", content: nextPrompt }]);
      setPrompt("");
      await consumeChatStream(streamChat(run.botId, sid, nextPrompt, reasoningEffort || null), sid, run);
      await refreshAfterRun(run, sid);
    } catch (err) {
      if (!ownsChatRun(run)) return;
      setStatus(errorMessage(err));
      await refreshBotState(run.botId).catch(() => null);
    } finally {
      finishChatRun(run);
    }
  }

  async function onSubmitPendingAnswer(option?: { label: string; value: string }) {
    if (!sessionId || !pendingQuestion || chatRunRef.current?.kind === "answer") return;
    const answerText = option ? option.label : pendingAnswerDraft.trim();
    if (!answerText) return;
    setStatus(null);
    const run = beginChatRun("answer");
    const optimisticQuestion = pendingQuestion;
    markSessionStateDirty();
    setPendingQuestion(null);
    setPendingAnswerDraft("");
    setPendingCustomOpen(false);
    setMessages((prev) => [...prev, { role: "user", content: answerText }]);
    try {
      await consumeChatStream(submitPendingQuestionAnswerStream(run.botId, sessionId, {
        question_id: optimisticQuestion.question_id,
        answer_text: answerText,
        selected_option_value: option?.value ?? null,
      }), sessionId, run);
      await refreshAfterRun(run, sessionId);
    } catch (err) {
      if (!ownsChatRun(run)) return;
      setStatus(errorMessage(err));
      await selectSession(sessionId, { force: true, run }).catch(() => null);
      if (ownsChatRun(run)) await refreshSessions(run.botId).catch(() => null);
      if (ownsChatRun(run)) await refreshBotState(run.botId).catch(() => null);
    } finally {
      finishChatRun(run);
    }
  }

  async function onStop() {
    const run = chatRunRef.current;
    if (!sessionId || !run) return;
    try {
      await cancelSession(run.botId, sessionId);
      if (!ownsChatRun(run)) return;
      setGeneratingRun(null);
      clearToolApprovals(run.botId, sessionId);
    } catch (err) {
      if (ownsChatRun(run)) setStatus(errorMessage(err));
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

  async function onRegenerateMessage(messageId: string) {
    if (!sessionId || chatRunRef.current) return;
    const previousMessages = messages;
    const optimisticMessages = removeAssistantTurn(previousMessages, messageId, true);
    setMessageActionId(messageId);
    setStatus(t("chat.status.regeneratingReply"));
    const run = beginChatRun("regenerate");
    try {
      markSessionStateDirty();
      setReasoningOpenById({});
      setToolOpenById({});
      setMessages(optimisticMessages);
      setVisibleHistoryStartIndex(findHistoryWindowStart(optimisticMessages, INITIAL_VISIBLE_HISTORY_TURNS));
      syncContextSnapshot(sessionId, optimisticMessages);
      await consumeChatStream(
        streamRegenerateSessionMessage(run.botId, sessionId, messageId, reasoningEffort || null),
        sessionId,
        run,
      );
      await refreshAfterRun(run, sessionId);
      if (ownsChatRun(run)) setStatus(t("chat.status.regeneratedReply"));
    } catch (err) {
      if (!ownsChatRun(run)) return;
      setStatus(errorMessage(err));
      try {
        await selectSession(sessionId, { force: true, run });
      } catch {
        if (!ownsChatRun(run)) return;
        markSessionStateDirty();
        setMessages(previousMessages);
        setVisibleHistoryStartIndex(findHistoryWindowStart(previousMessages, INITIAL_VISIBLE_HISTORY_TURNS));
        syncContextSnapshot(sessionId, previousMessages);
      }
      if (ownsChatRun(run)) await refreshSessions(run.botId).catch(() => null);
      if (ownsChatRun(run)) await refreshBotState(run.botId).catch(() => null);
    } finally {
      finishChatRun(run);
    }
  }

  function requestDeleteSession(id: string) {
    if (!chatBusy) setDeletionRequest({ target: { kind: "session", sessionId: id }, state: "confirm" });
  }

  async function confirmDeletion() {
    if (!deletionRequest || deleting || chatBusy) return;
    const { target } = deletionRequest;
    if (target.kind === "turn" && sessionId !== target.sessionId) return;
    setDeletionRequest({ target, state: "submitting" });
    setStatus(null);
    try {
      if (target.kind === "turn") {
        const result = await deleteSessionMessage(botId, target.sessionId, target.messageId);
        if (botIdRef.current !== botId) return;
        markSessionStateDirty();
        setReasoningOpenById({});
        setToolOpenById({});
        setMessages(result.messages);
        setActivityMessageIndex(null);
        setVisibleHistoryStartIndex(findHistoryWindowStart(result.messages, INITIAL_VISIBLE_HISTORY_TURNS));
        syncContextSnapshot(target.sessionId, result.messages);
        setStatus(t("chat.status.deletedTurn"));
      } else {
        await deleteSession(botId, target.sessionId);
        if (botIdRef.current !== botId) return;
        if (sessionId === target.sessionId) {
          markSessionStateDirty();
          setSessionId(null);
          setReasoningOpenById({});
          setToolOpenById({});
          setActivityMessageIndex(null);
          setPendingQuestion(null);
          clearTodoState();
          setMessages([]);
          setVisibleHistoryStartIndex(0);
        }
        setContextBySession((prev) => {
          const next = { ...prev };
          delete next[target.sessionId];
          return next;
        });
      }
    } catch (err) {
      if (botIdRef.current === botId) setDeletionRequest({ target, state: "error", message: errorMessage(err) });
      return;
    }
    setDeletionRequest(null);
    await refreshSessions(botId).catch((err) => {
      if (botIdRef.current === botId) setStatus(errorMessage(err));
    });
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

  const sessionQuery = sessionSearch.trim().toLocaleLowerCase(sessionLocale);
  const listedSessions = sortedSessions.filter((item) =>
    (item.preview ?? "").toLocaleLowerCase(sessionLocale).includes(sessionQuery)
    || item.session_id.toLocaleLowerCase(sessionLocale).includes(sessionQuery),
  );
  const renderSessionList = (tooltipContainer?: HTMLElement | null) => listedSessions.length ? listedSessions.map((item) => (
    <div key={item.session_id} className="chat-session-row">
      <button
        type="button"
        className="chat-session-select"
        aria-current={sessionId === item.session_id ? "true" : undefined}
        onClick={() => {
          setMobileSessionsOpen(false);
          setSessionId(item.session_id);
        }}
        disabled={chatBusy}
        title={item.session_id}
      >
        <span className="block truncate font-medium">{item.preview || item.session_id}</span>
        <span className="chat-session-meta">{t("chat.sessions.messages", { count: item.message_count })}</span>
      </button>
      <IconButton
        icon={Trash2}
        label={t("chat.sessions.deleteTitle")}
        className="chat-session-delete icon-button-danger"
        aria-haspopup="dialog"
        tooltipContainer={tooltipContainer}
        onClick={() => {
          setMobileSessionsOpen(false);
          requestDeleteSession(item.session_id);
        }}
        disabled={chatBusy}
      />
    </div>
  )) : <p className="chat-sessions-empty">{t(sessionQuery ? "chat.sessions.noMatch" : "chat.sessions.empty")}</p>;

  const renderSessionActions = (tooltipContainer?: HTMLElement | null) => (
    <div className="chat-sidebar-actions">
      <button type="button" className="chat-button chat-button-quiet chat-new-session" onClick={() => void startNewSession()} disabled={chatBusy || chatBlocked}>
        <ChatGlyph name="write" />
        {t("chat.sessions.new")}
      </button>
      <IconButton
        icon={Search}
        label={t("chat.sessions.search")}
        tooltipContainer={tooltipContainer}
        onClick={() => {
          setSessionSearchOpen(!sessionSearchOpen);
          if (sessionSearchOpen) setSessionSearch("");
        }}
        aria-expanded={sessionSearchOpen}
      />
      <IconButton
        icon={RefreshCw}
        label={t("chat.sessions.refresh")}
        tooltipContainer={tooltipContainer}
        onClick={() => void refreshSessions(botId).catch((err) => setStatus(errorMessage(err)))}
        disabled={chatBusy}
      />
    </div>
  );

  function renderSessionSearch(visible: boolean) {
    return sessionSearchOpen && visible ? (
      <input
        ref={sessionSearchRef}
        className="chat-session-search"
        type="search"
        value={sessionSearch}
        aria-label={t("chat.sessions.search")}
        placeholder={t("chat.sessions.search")}
        onChange={(event) => setSessionSearch(event.target.value)}
      />
    ) : null;
  }

  const todoDockContent = todoVisible && todoSnapshot ? (
    <section className="chat-plan">
      <header>
        <h2>{todoDone ? t("chat.todo.doneTitle") : todoSnapshot.title}</h2>
        <span>{todoSnapshot.completed} / {todoSnapshot.total}</span>
      </header>
      <p>{todoDone
        ? todoCompletedAtLabel
          ? `${t("chat.todo.completedState")} · ${todoCompletedAtLabel}`
          : t("chat.todo.completedState")
        : activeTodoItem?.detail ?? t("chat.todo.idle")}</p>
      <progress value={todoPercent} max={100} aria-label={t("chat.todo.dockLabel")} />
      <ol>
        {todoSnapshot.items.map((item) => (
          <li key={item.id} data-state={item.status}>
            <span>
              {item.label}
              {item.detail ? <small>{item.detail}</small> : null}
            </span>
            <span>{t(item.status === "done" ? "chat.todo.completedState" : item.status === "active" ? "chat.todo.activeState" : "chat.todo.pendingState")}</span>
          </li>
        ))}
      </ol>
    </section>
  ) : null;

  const currentSession = sortedSessions.find((item) => item.session_id === sessionId);
  const sessionTitle = currentSession?.preview?.trim() || messages.find((message) => message.role === "user")?.content.trim()
    || t("chat.empty.title");
  const modelName = selectedModel?.label ?? botConfig?.model;
  const composerDisabled = chatBusy || chatBlocked || awaitingUserAnswer;

  const modelControl = (
    <details className="chat-settings">
      <summary className="chat-button chat-button-quiet">
        <span className="chat-settings-model">{modelName || t("chat.settings.title")}</span>
        <ChatGlyph name="down" />
      </summary>
      <div className="chat-settings-panel">
        <div className="chat-settings-fields">
          <label>
            <span>{t("chat.model.label")}</span>
            <select
              value={botConfig?.chat_model_id ?? ""}
              onChange={(e) => void onSelectModel(e.target.value)}
              className="workspace-select"
              disabled={chatBusy || modelSaving}
            >
              <option value="">{t("chat.model.fallbackOption")}</option>
              {availableModels.map((item) => (
                <option key={item.model_id} value={item.model_id}>
                  {item.provider_name} / {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t("chat.reasoningEffort.label")}</span>
            <select
              value={reasoningEffort}
              onChange={(e) => {
                const effort = e.target.value;
                if (effort === "" || effort === "low" || effort === "medium" || effort === "high") {
                  setReasoningEffort(effort);
                }
              }}
              className="workspace-select"
              disabled={chatBusy}
            >
              <option value="">{t("chat.reasoningEffort.auto")}</option>
              <option value="low">{t("chat.reasoningEffort.low")}</option>
              <option value="medium">{t("chat.reasoningEffort.medium")}</option>
              <option value="high">{t("chat.reasoningEffort.high")}</option>
            </select>
          </label>
        </div>
        {modelName ? (
          <p>
            {selectedModel
              ? t("chat.model.usingSelected", { provider: selectedModel.provider_name, label: selectedModel.label })
              : t("chat.model.usingFallback", { model: modelName })}
          </p>
        ) : null}
      </div>
    </details>
  );

  function renderToolApprovals() {
    return visibleToolApprovals.length > 0 ? (
      <div className="chat-approvals">
        {visibleToolApprovals.map((approval) => (
          <ToolApprovalCard
            key={approval.approval_id}
            approval={approval}
            onDecision={(approved) => void onSubmitToolApproval(approval, approved)}
          />
        ))}
      </div>
    ) : null;
  }

  function renderActivityJump(count: number, messageIndex: number) {
    return (
      <button type="button" className="chat-activity-jump" onClick={() => openInspector("activity", messageIndex)}>
        <ChatGlyph name="activity" />
        <span>{t("chat.inspector.activity")}</span>
        <span>{t("chat.inspector.count", { count })}</span>
        <ChatGlyph name="right" />
      </button>
    );
  }

  const inspectorItems: InspectorPane[] = todoVisible ? ["activity", "context", "plan"] : ["activity", "context"];
  const inspectorContent = (
    <>
      <nav className="chat-inspector-tabs" aria-label={t("chat.inspector.label")}>
        {inspectorItems.map((pane) => (
          <button
            key={pane}
            type="button"
            aria-pressed={activeInspectorPane === pane}
            onClick={() => {
              setInspectorPane(pane);
              if (pane === "activity") setActivityMessageIndex(null);
            }}
          >
            {t(`chat.inspector.${pane}`)}
          </button>
        ))}
        {!desktop ? (
          <IconButton
            icon={X}
            label={t("chat.inspector.hide")}
            className="chat-inspector-close"
            tooltipContainer={mobileInspectorDialog.current}
            onClick={() => setMobileInspectorOpen(false)}
          />
        ) : null}
      </nav>
      <div className="chat-inspector-body">
        {activeInspectorPane === "activity" ? (
          <>
            <div className="chat-inspector-tools">
              <div className="chat-inspector-heading">
                <h2>{t(activityMessageIndex == null ? "chat.inspector.currentTurn" : "chat.inspector.selectedTurn")}</h2>
                {activityMessageIndex != null ? (
                  <button type="button" className="chat-button chat-button-quiet" onClick={() => setActivityMessageIndex(null)}>
                    {t("chat.inspector.backToCurrent")}
                  </button>
                ) : null}
              </div>
              {activityInvocations.length > 0 ? (
                <ul className="chat-tool-list">
                  {activityInvocations.map((invocation) => (
                    <ToolInvocationPanel
                      key={invocation.key}
                      invocation={invocation}
                      open={Boolean(toolOpenById[invocation.key])}
                      onToggle={() => toggleToolInvocation(invocation.key)}
                    />
                  ))}
                </ul>
              ) : <p className="chat-inspector-empty">{t("chat.inspector.empty")}</p>}
            </div>
            {approvalsInInspector ? renderToolApprovals() : null}
          </>
        ) : activeInspectorPane === "context" ? (
          <div className="chat-inspector-context">
            <dl>
              <dt>{t("app.bot.select")}</dt><dd>{botName ?? botId}</dd>
              {modelName ? <><dt>{t("chat.model.label")}</dt><dd>{modelName}</dd></> : null}
            </dl>
            <div className="chat-context">
              <div className="chat-context-label">
                <span>{t("chat.context.label")}</span>
                <span>{contextPercent == null ? "–" : t("chat.context.percent", { percent: contextPercent })}</span>
              </div>
              <div className="chat-context-track" aria-hidden="true">
                <div data-tone={contextTone} style={{ width: `${contextPercent ?? 0}%` }} />
              </div>
              <div className="chat-context-label">
                <span>{t(contextStatusKey)}</span>
                {contextThresholdTokens ? (
                  <span title={t("chat.context.threshold", { count: contextThresholdTokens })}>
                    {activeContextSnapshot ? new Intl.NumberFormat(sessionLocale).format(activeContextSnapshot.totalTokens) : "–"}
                    {" / "}
                    {new Intl.NumberFormat(sessionLocale).format(contextThresholdTokens)}
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        ) : todoDockContent}
      </div>
    </>
  );

  return (
    <div className="chat-workspace flex h-full overflow-hidden">
      <aside
        className={classNames("chat-sidebar hidden shrink-0 lg:flex", sidebarCollapsed && "chat-sidebar-collapsed")}
        aria-label={t("chat.sessions.title")}
      >
        {sidebarCollapsed ? (
          <div className="chat-session-rail">
            <IconButton
              icon={PanelLeftOpen}
              label={t("chat.sessions.expand")}
              onClick={() => setSidebarCollapsed(false)}
            />
            <button
              type="button"
              className="chat-button chat-button-quiet chat-icon-button"
              onClick={() => void startNewSession()}
              disabled={chatBusy || chatBlocked}
              aria-label={t("chat.sessions.new")}
              title={t("chat.sessions.new")}
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <path d="M10 4.5v11m-5.5-5.5h11" strokeLinecap="round" />
              </svg>
            </button>
            <div className="chat-session-rail-list">
              {sortedSessions.map((item) => (
                <button
                  key={item.session_id}
                  type="button"
                  className="chat-button chat-icon-button"
                  aria-current={sessionId === item.session_id ? "true" : undefined}
                  onClick={() => setSessionId(item.session_id)}
                  disabled={chatBusy}
                  title={item.preview || item.session_id}
                >
                  {item.session_id.slice(0, 2).toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-sidebar-panel">
            <div className="chat-sidebar-head">
              <div className="chat-sidebar-heading">
                <h2>{t("chat.sessions.title")}</h2>
                <IconButton
                  icon={PanelLeftClose}
                  label={t("chat.sessions.collapse")}
                  onClick={() => setSidebarCollapsed(true)}
                />
              </div>
              {renderSessionActions()}
            </div>
            {renderSessionSearch(desktop)}
            <div className="chat-sessions-list">{renderSessionList()}</div>
          </div>
        )}
      </aside>

      <section className="chat-conversation flex min-w-0 flex-1 flex-col">
        <header className="chat-topbar">
          <div className="chat-toolbar">
            <div className="flex shrink-0 items-center gap-1.5 lg:hidden">
              <IconButton
                icon={PanelLeft}
                label={t("chat.sessions.mobileOpen")}
                aria-haspopup="dialog"
                onClick={() => setMobileSessionsOpen(true)}
              />
            </div>
            <div className="chat-heading">
              {messages.length > 0 || isGenerating ? <h1 title={sessionTitle}>{sessionTitle}</h1> : null}
              <p>{botName ?? botId}</p>
            </div>
            {todoVisible ? (
              <button
                type="button"
                className="chat-button chat-button-quiet chat-plan-trigger"
                aria-label={t("chat.todo.openMobile")}
                onClick={() => openInspector("plan")}
              >
                {t("chat.todo.dockLabel")} <span>{todoSnapshot?.completed}/{todoSnapshot?.total}</span>
              </button>
            ) : null}
            <IconButton
              icon={PanelRight}
              label={t(inspectorOpen ? "chat.inspector.hide" : "chat.inspector.show")}
              className="chat-inspector-toggle"
              aria-expanded={inspectorOpen}
              aria-haspopup={desktop ? undefined : "dialog"}
              onClick={() => {
                if (desktop) setDesktopInspectorOpen(!desktopInspectorOpen);
                else setMobileInspectorOpen(!mobileInspectorOpen);
              }}
            />
          </div>
          {blockedReason ? <p className="chat-notice chat-notice-warning" role="status">{blockedReason}</p> : null}
          {status ? <p className="chat-notice" role="status">{status}</p> : null}
        </header>

        <div ref={scrollRef} className="chat-scroll min-h-0 flex-1 overflow-auto">
          <div className={classNames("chat-column chat-transcript", messages.length === 0 && "chat-transcript-empty")}>
            {hiddenHistoryTurnCount > 0 ? (
              <div className="flex justify-center">
                <button
                  type="button"
                  className="chat-button chat-button-quiet"
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
                const showAssistantCard = hasVisibleAssistantContent(message) || Boolean(
                  usage && (assistantInvocations.length === 0 || (!chatBusy && messageId === latestRegeneratableAssistantId)),
                );
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
                const interruptedAssistant = message.completion_state === "interrupted";


                return (
                  <div key={message.message_id ?? `assistant-${messageIndex}`} className="flex w-full justify-start">
                    <div
                      className="chat-assistant flex w-full min-w-0 flex-col gap-3"
                    >
                      {showAssistantCard ? (
                        <div className="chat-assistant-copy">
                          <div className="chat-message-author">{t("app.brand")}</div>
                          {message.is_compaction ? (
                            <div className="chat-message-note">
                              {t("chat.context.compacted")}
                            </div>
                          ) : null}
                          {interruptedAssistant ? (
                            <div className="chat-message-note chat-notice-warning">
                              {t("chat.message.interrupted")}
                            </div>
                          ) : null}
                          {showReasoning && messageId ? (
                            <div className="chat-reasoning">
                              <button
                                type="button"
                                className="chat-reasoning-toggle"
                                aria-expanded={reasoningOpen}
                                onClick={() => toggleReasoning(messageId)}
                              >
                                <span>{t("chat.reasoning.title")}</span>
                                <ChatGlyph name="down" />
                              </button>
                              {reasoningOpen ? (
                                <div className="chat-reasoning-content">
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

                          <div className="chat-message-actions">
                            {assistantText || canRegenerate || canDelete ? (
                              <div className="chat-message-action-buttons" role="group" aria-label={t("chat.message.actions")}>
                                {assistantText ? (
                                  <IconButton
                                    icon={Copy}
                                    label={t("chat.message.copy")}
                                    onClick={() => void onCopyMessage(message)}
                                    disabled={actionBusy}
                                  />
                                ) : null}
                                {messageId && canRegenerate ? (
                                  <IconButton
                                    icon={RotateCcw}
                                    label={t("chat.message.regenerate")}
                                    onClick={() => void onRegenerateMessage(messageId)}
                                    disabled={actionBusy}
                                  />
                                ) : null}
                                {messageId && sessionId && canDelete ? (
                                  <IconButton
                                    icon={Trash2}
                                    label={t("chat.message.deleteTurn")}
                                    className="icon-button-danger"
                                    aria-haspopup="dialog"
                                    onClick={() => setDeletionRequest({
                                      target: { kind: "turn", sessionId, messageId },
                                      state: "confirm",
                                    })}
                                    disabled={actionBusy}
                                  />
                                ) : null}
                              </div>
                            ) : null}
                            {usage ? (
                              <div className="ml-auto flex flex-wrap items-center gap-2">
                                {usage.total != null ? (
                                  <span className="chat-token-value">
                                    {t("chat.message.tokens", { count: usage.total })}
                                  </span>
                                ) : null}
                                {usage.prompt != null ? (
                                  <span className="chat-token-value">↑ {usage.prompt}</span>
                                ) : null}
                                {usage.completion != null ? (
                                  <span className="chat-token-value">↓ {usage.completion}</span>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      ) : null}

                      {inlinePendingQuestion ? renderPendingQuestionCard(inlinePendingQuestion) : null}

                      {assistantInvocations.length > 0 ? renderActivityJump(assistantInvocations.length, messageIndex) : null}
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

                return <div key={invocation.key}>{renderActivityJump(1, messageIndex)}</div>;
              }

              return (
                <div key={message.message_id ?? `user-${messageIndex}`} className="chat-user-message">
                  <div className="chat-message-author">{t("chat.message.you")}</div>
                  <div className="whitespace-pre-wrap">{message.content}</div>
                </div>
              );
            })}

            {isGenerating ? <GenerationIndicator label={t("chat.model.generating")} /> : null}

            {messages.length === 0 && !isGenerating ? (
              <div className="chat-welcome">
                <h1>{t(chatBlocked ? "chat.empty.unavailable" : "chat.empty.title")}</h1>
                {chatBlocked ? <p>{t("chat.empty.blocked")}</p> : (
                  <ul className="chat-starters" aria-label={t("chat.starter.group")}>
                    {STARTER_PROMPTS.map(({ key, icon: Icon }) => (
                      <li key={key}>
                        <button
                          type="button"
                          className="chat-starter"
                          onClick={() => {
                            setPrompt(t(`chat.starter.${key}.prompt`));
                            composerRef.current?.focus();
                          }}
                          disabled={composerDisabled || prompt.length > 0}
                        >
                          <Icon size={16} strokeWidth={1.65} aria-hidden="true" focusable="false" />
                          <span>{t(`chat.starter.${key}.label`)}</span>
                          <ArrowDownLeft
                            className="chat-starter-insert"
                            size={14}
                            strokeWidth={1.65}
                            aria-hidden="true"
                            focusable="false"
                          />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}
          </div>
        </div>

        <div className="chat-composer-dock">
          {pendingQuestion && showPendingQuestionFallback ? (
            <div className="chat-column mb-3">{renderPendingQuestionCard(pendingQuestion)}</div>
          ) : null}

          {!approvalsInInspector ? renderToolApprovals() : null}
          {toolApprovalNotice?.botId === botId && toolApprovalNotice.sessionId === sessionId ? (
            <p role="status" className="chat-column mb-3 break-words text-xs text-zinc-400">
              {toolApprovalNotice.text}
            </p>
          ) : null}

          <div className="chat-column chat-composer">
            <textarea
              ref={composerRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              aria-label={t("chat.input.ready")}
              aria-describedby={composerHintId}
              placeholder={chatBlocked ? t("chat.input.blocked") : awaitingUserAnswer ? t("chat.input.answerPending") : t("chat.input.ready")}
              rows={1}
              disabled={composerDisabled}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                  e.preventDefault();
                  void onSend();
                }
              }}
            />
            <div className="chat-composer-actions">
              {modelControl}
              {activeContextSnapshot ? (
                <span className="chat-composer-usage">
                  {t("chat.message.tokens", { count: new Intl.NumberFormat(sessionLocale).format(activeContextSnapshot.totalTokens) })}
                </span>
              ) : null}
              {streaming ? (
                <button type="button" className="chat-button chat-button-stop" onClick={() => void onStop()}>
                  {t("chat.model.stop")}
                  <ChatGlyph name="stop" />
                </button>
              ) : (
                <IconButton
                  className="chat-button chat-button-primary chat-composer-send"
                  icon={ArrowUp}
                  label={t("chat.input.send")}
                  onClick={() => void onSend()}
                  disabled={composerDisabled || !prompt.trim()}
                />
              )}
            </div>
          </div>
          <p className="sr-only" id={composerHintId}>{t("chat.input.hint")}</p>
        </div>
      </section>

      {desktop && desktopInspectorOpen ? (
        <aside className="chat-inspector" aria-label={t("chat.inspector.label")}>
          {inspectorContent}
        </aside>
      ) : null}

      <dialog
        ref={mobileSessionsDialog}
        className="chat-sessions-dialog"
        aria-label={t("chat.sessions.title")}
        onClose={() => setMobileSessionsOpen(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setMobileSessionsOpen(false);
        }}
      >
        {mobileSessionsOpen ? (
          <div className="chat-sidebar-panel chat-sessions-sheet">
            <div className="chat-sidebar-head">
              <div className="chat-sidebar-heading">
                <h2>{t("chat.sessions.title")}</h2>
                <IconButton
                  icon={X}
                  label={t("chat.sessions.close")}
                  tooltipContainer={mobileSessionsDialog.current}
                  onClick={() => setMobileSessionsOpen(false)}
                />
              </div>
              {renderSessionActions(mobileSessionsDialog.current)}
            </div>
            {renderSessionSearch(!desktop && mobileSessionsOpen)}
            <div className="chat-sessions-list">{renderSessionList(mobileSessionsDialog.current)}</div>
          </div>
        ) : null}
      </dialog>

      {!desktop ? (
        <dialog
          ref={mobileInspectorDialog}
          className="chat-inspector-dialog"
          aria-label={t("chat.inspector.label")}
          onClose={() => setMobileInspectorOpen(false)}
          onClick={(event) => {
            if (event.target === event.currentTarget) setMobileInspectorOpen(false);
          }}
        >
          <div className="chat-inspector chat-inspector-sheet">{mobileInspectorOpen ? inspectorContent : null}</div>
        </dialog>
      ) : null}

      <dialog
        ref={deleteDialog}
        className="workspace-dialog chat-delete-dialog"
        aria-labelledby={`${deleteDialogId}-title`}
        aria-describedby={`${deleteDialogId}-description`}
        onCancel={(event) => {
          event.preventDefault();
          if (!deleting) setDeletionRequest(null);
        }}
        onClick={(event) => {
          if (!deleting && event.target === event.currentTarget) setDeletionRequest(null);
        }}
      >
        {deletionRequest ? (
          <div className="chat-delete-dialog-content">
            <h2 id={`${deleteDialogId}-title`} className="workspace-dialog-title">
              {t(deletionRequest.target.kind === "turn" ? "chat.deleteTurn.title" : "chat.deleteSession.title")}
            </h2>
            <p id={`${deleteDialogId}-description`}>
              {deletionRequest.target.kind === "turn"
                ? t("chat.deleteTurn.description")
                : t("chat.deleteSession.description", { sessionId: deletionRequest.target.sessionId })}
            </p>
            {deletionRequest.state === "error" ? (
              <p className="chat-delete-error" role="alert">{deletionRequest.message}</p>
            ) : null}
            <div className="chat-delete-actions">
              <button type="button" className="chat-button" disabled={deleting} onClick={() => setDeletionRequest(null)}>
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="chat-button chat-button-delete"
                disabled={deleting || chatBusy}
                onClick={() => void confirmDeletion()}
              >
                {t(deleting ? "chat.delete.submitting" : "common.delete")}
              </button>
            </div>
          </div>
        ) : null}
      </dialog>
    </div>
  );
}
