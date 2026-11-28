import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
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
  Message,
  PendingQuestion,
  ReasoningEffort,
  StreamEvent,
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
import { ToolApprovalCard } from "./ToolApprovalCard";
import { countHiddenTurnsBeforeIndex } from "./chatHistoryWindow";
import {
  ChatSessionState,
  contextSnapshotFromMessages,
  type ChatBot,
  type ChatRun,
  type ChatSelection,
  type PendingToolApproval,
} from "./chatSessionState";
import { buildToolRenderState, type ToolInvocationView } from "./chatToolState";

const DESKTOP_CHAT = "(min-width: 1024px)";
const STARTER_PROMPTS = [
  { key: "explore", icon: FolderSearch },
  { key: "diagnose", icon: FlaskConical },
  { key: "plan", icon: ListChecks },
] as const;

type InspectorPane = "activity" | "context" | "plan";
type DeletionTarget =
  | { kind: "session"; sessionId: string }
  | { kind: "turn"; sessionId: string; messageId: string };
type DeletionRequest =
  | { target: DeletionTarget; state: "confirm" | "submitting" }
  | { target: DeletionTarget; state: "error"; message: string };

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

function usageParts(usage: Usage | null | undefined) {
  const total = usage?.total_tokens ?? null;
  const prompt = usage?.prompt_tokens ?? null;
  const completion = usage?.completion_tokens ?? null;
  if (total == null && prompt == null && completion == null) return null;
  return { total, prompt, completion };
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

  const [chat] = useState(() => new ChatSessionState(botId));
  const {
    selection, sessions, messages, visibleHistoryStartIndex, contextBySession, activeRun, status,
    pendingQuestion, todoSnapshot, toolApprovals: visibleToolApprovals, toolApprovalNotice,
  } = useSyncExternalStore(chat.subscribe, chat.getSnapshot);
  const { sessionId } = selection;
  const messageActionId = activeRun?.token.kind === "regenerate" ? activeRun.token.messageId : null;

  const [prompt, setPrompt] = useState("");
  const [botConfig, setBotConfig] = useState<BotConfig | null>(null);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort | "">("");
  const [modelSaving, setModelSaving] = useState(false);
  const [deletionRequest, setDeletionRequest] = useState<DeletionRequest | null>(null);
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
  const [pendingAnswerDraft, setPendingAnswerDraft] = useState("");
  const [pendingCustomOpen, setPendingCustomOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const composerHintId = useId();
  const mobileSessionsDialog = useRef<HTMLDialogElement | null>(null);
  const mobileInspectorDialog = useRef<HTMLDialogElement | null>(null);
  const deleteDialog = useRef<HTMLDialogElement | null>(null);
  const deleteDialogId = useId();
  const sessionSearchRef = useRef<HTMLInputElement | null>(null);

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

  const streaming = Boolean(activeRun);
  const submittingAnswer = activeRun?.token.kind === "answer";
  const chatBusy = streaming;
  const deletionDialogOpen = deletionRequest !== null;
  const deleting = deletionRequest?.state === "submitting";
  const chatBlocked = Boolean(botConfig && (!botConfig.enabled || !botConfig.chat_ready));
  const blockedReason =
    botConfig?.chat_disabled_reason ?? (botConfig?.enabled === false ? t("chat.fallbackDisabledReason") : null);
  const awaitingUserAnswer = Boolean(pendingQuestion);
  const isGenerating = streaming && !awaitingUserAnswer && visibleToolApprovals.length === 0
    && Boolean(activeRun?.generating);
  const approvalsInInspector = inspectorOpen && activeInspectorPane === "activity";
  const pendingQuestionMessageId = pendingQuestion?.message_id ?? null;
  const showPendingQuestionFallback = Boolean(
    pendingQuestion && (!pendingQuestionMessageId || !messages.some((item) => item.role === "assistant" && item.message_id === pendingQuestionMessageId)),
  );

  const resetMessagePresentation = useCallback(() => {
    setReasoningOpenById({});
    setToolOpenById({});
    setActivityMessageIndex(null);
  }, []);

  const refreshSessions = useCallback(async (bot: ChatBot) => {
    const ticket = chat.beginSessionListLoad(bot);
    if (!ticket) return;
    try {
      chat.acceptSessionList(ticket, await listSessions(bot.id));
    } catch (err) {
      if (chat.ownsSessionListLoad(ticket)) throw err;
    }
  }, [chat]);

  const refreshBotState = useCallback(async (bot: ChatBot) => {
    const [cfg, models] = await Promise.all([getBotConfig(bot.id), listAvailableModels()]);
    if (!chat.ownsBot(bot)) return;
    setBotConfig(cfg);
    setAvailableModels(models);
  }, [chat]);

  const loadSaved = useCallback(async (target: ChatSelection, run?: ChatRun) => {
    if (!target.sessionId) return;
    const ticket = chat.beginLoad(target, run);
    if (!ticket) return;
    const id = target.sessionId;
    try {
      const [saved, question] = await Promise.all([
        loadSession(target.bot.id, id),
        getPendingQuestion(target.bot.id, id),
      ]);
      if (chat.acceptSaved(ticket, saved.messages, question)) resetMessagePresentation();
    } catch (err) {
      if (chat.ownsLoad(ticket)) throw err;
    }
  }, [chat, resetMessagePresentation]);

  async function startNewSession() {
    const bot = selection.bot;
    const creation = chat.beginSessionCreation(bot);
    if (!creation) return;
    chat.reportStatus(null);
    let created: Awaited<ReturnType<typeof createSession>>;
    try {
      created = await createSession(bot.id);
    } catch (err) {
      if (chat.finishSessionCreation(creation)) {
        setMobileSessionsOpen(false);
        chat.reportStatus(errorMessage(err));
      }
      return;
    }
    if (!chat.ownsBot(bot)) return;
    if (chat.activateCreatedSession(creation, created.session_id)) {
      resetMessagePresentation();
      setMobileSessionsOpen(false);
    }
    await refreshSessions(bot).catch((err) => {
      if (chat.ownsBot(bot)) chat.reportStatus(errorMessage(err));
    });
  }

  useLayoutEffect(() => {
    const bot = chat.selectBot(botId);
    resetMessagePresentation();
    setMobileInspectorOpen(false);
    setInspectorPane("activity");
    setSessionSearch("");
    setSessionSearchOpen(false);
    setDeletionRequest(null);
    setReasoningEffort("");
    setModelSaving(false);
    setBotConfig(null);
    setMobileSessionsOpen(false);
    Promise.all([refreshSessions(bot), refreshBotState(bot)]).catch((err) => {
      if (chat.ownsBot(bot)) chat.reportStatus(errorMessage(err));
    });
  }, [botId, chat, refreshBotState, refreshSessions, resetMessagePresentation]);

  useEffect(() => {
    loadSaved(selection).catch((err) => {
      if (chat.ownsSelection(selection)) chat.reportStatus(errorMessage(err));
    });
  }, [chat, loadSaved, selection]);

  const pendingQuestionId = pendingQuestion?.question_id;
  useEffect(() => {
    setPendingAnswerDraft("");
    setPendingCustomOpen(false);
  }, [selection, pendingQuestionId]);

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

  async function consumeChatStream(events: AsyncIterable<StreamEvent>, streamSessionId: string, run: ChatRun) {
    try {
      for await (const event of events) {
        const projected = chat.applyStreamEvent(run, streamSessionId, event);
        if (projected && event.type === "tool_approval_request" && desktop) openInspector("activity");
      }
    } finally {
      chat.endStreaming(run, streamSessionId);
    }
  }

  async function refreshAfterRun(run: ChatRun, target: ChatSelection) {
    if (!chat.ownsRun(run)) return;
    await refreshSessions(run.bot);
    if (!chat.ownsRun(run)) return;
    await loadSaved(target, run);
    if (chat.ownsRun(run)) await refreshBotState(run.bot);
  }

  async function refreshAfterFailedRun(run: ChatRun) {
    if (chat.ownsRun(run)) await refreshSessions(run.bot).catch(() => null);
    if (chat.ownsRun(run)) await refreshBotState(run.bot).catch(() => null);
  }

  async function onSubmitToolApproval(approval: PendingToolApproval, approved: boolean) {
    const decision = chat.beginApproval(approval);
    if (!decision) return;
    try {
      const result = await submitToolApproval({
        botId: decision.selection.bot.id,
        sessionId: approval.session_id,
        approvalId: decision.id,
        approved,
      });
      chat.finishApproval(decision, result);
    } catch {
      chat.finishApproval(decision, "error");
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
    const nextPrompt = prompt.trim();
    if (!nextPrompt || chatBlocked) return;
    const run = chat.beginSend(selection.bot);
    if (!run) return;
    setActivityMessageIndex(null);
    try {
      if (!chat.getSnapshot().selection.sessionId) {
        const created = await createSession(run.bot.id);
        if (!chat.bindSendSession(run, created.session_id)) return;
        resetMessagePresentation();
        await refreshSessions(run.bot);
      }
      const target = chat.getSnapshot().selection;
      const id = target.sessionId;
      if (!id || !chat.recordSend(run, nextPrompt)) return;
      setPrompt("");
      await consumeChatStream(streamChat(run.bot.id, id, nextPrompt, reasoningEffort || null), id, run);
      await refreshAfterRun(run, target);
    } catch (err) {
      if (!chat.ownsRun(run)) return;
      chat.reportStatus(errorMessage(err));
      await refreshBotState(run.bot).catch(() => null);
    } finally {
      chat.finishRun(run);
    }
  }

  async function onSubmitPendingAnswer(option?: { label: string; value: string }) {
    const answerText = option ? option.label : pendingAnswerDraft.trim();
    if (!answerText) return;
    const run = chat.beginAnswer(selection.bot, answerText);
    if (!run) return;

    setActivityMessageIndex(null);
    setPendingAnswerDraft("");
    setPendingCustomOpen(false);
    try {
      await consumeChatStream(submitPendingQuestionAnswerStream(run.bot.id, run.sessionId, {
        question_id: run.questionId,
        answer_text: answerText,
        selected_option_value: option?.value ?? null,
      }), run.sessionId, run);
      await refreshAfterRun(run, selection);
    } catch (err) {
      if (!chat.ownsRun(run)) return;
      chat.reportStatus(errorMessage(err));
      await loadSaved(selection, run).catch(() => null);
      await refreshAfterFailedRun(run);
    } finally {
      chat.finishRun(run);
    }
  }

  async function onStop() {
    const current = chat.getSnapshot();
    const run = current.activeRun?.token;
    const id = current.selection.sessionId;
    if (!id || !run) return;
    try {
      await cancelSession(run.bot.id, id);
      chat.endStreaming(run, id);
    } catch (err) {
      if (chat.ownsRun(run)) chat.reportStatus(errorMessage(err));
    }
  }

  async function onCopyMessage(message: Message) {
    if (!message.content?.trim()) return;
    try {
      await copyText(message.content);
      if (chat.ownsSelection(selection)) chat.reportStatus(t("chat.status.copiedReply"));
    } catch (err) {
      if (chat.ownsSelection(selection)) chat.reportStatus(errorMessage(err));
    }
  }

  async function onRegenerateMessage(messageId: string) {
    const run = chat.beginRegenerate(selection.bot, messageId);
    if (!run) return;
    resetMessagePresentation();
    chat.reportStatus(t("chat.status.regeneratingReply"));
    try {
      await consumeChatStream(
        streamRegenerateSessionMessage(run.bot.id, run.sessionId, messageId, reasoningEffort || null),
        run.sessionId,
        run,
      );
      await refreshAfterRun(run, selection);
      if (chat.ownsRun(run)) chat.reportStatus(t("chat.status.regeneratedReply"));
    } catch (err) {
      if (!chat.ownsRun(run)) return;
      chat.reportStatus(errorMessage(err));
      try {
        await loadSaved(selection, run);
      } catch {
        chat.rollbackRegeneration(run);
      }
      await refreshAfterFailedRun(run);
    } finally {
      chat.finishRun(run);
    }
  }

  function requestDeleteSession(id: string) {
    if (!chatBusy) setDeletionRequest({ target: { kind: "session", sessionId: id }, state: "confirm" });
  }

  async function confirmDeletion() {
    if (!deletionRequest || deleting || chatBusy) return;
    const { target } = deletionRequest;
    const bot = selection.bot;
    if (target.kind === "turn" && sessionId !== target.sessionId) return;
    setDeletionRequest({ target, state: "submitting" });
    chat.reportStatus(null);
    try {
      if (target.kind === "turn") {
        const result = await deleteSessionMessage(bot.id, target.sessionId, target.messageId);
        if (!chat.ownsBot(bot)) return;
        if (chat.replaceDeletedTurn(selection, result.messages)) {
          resetMessagePresentation();
          chat.reportStatus(t("chat.status.deletedTurn"));
        }
      } else {
        await deleteSession(bot.id, target.sessionId);
        if (!chat.ownsBot(bot)) return;
        chat.removeSession(bot, target.sessionId);
        if (sessionId === target.sessionId) resetMessagePresentation();
      }
    } catch (err) {
      if (chat.ownsBot(bot)) setDeletionRequest({ target, state: "error", message: errorMessage(err) });
      return;
    }
    setDeletionRequest(null);
    await refreshSessions(bot).catch((err) => {
      if (chat.ownsBot(bot)) chat.reportStatus(errorMessage(err));
    });
  }

  async function onSelectModel(nextModelId: string) {
    if (chatBusy) return;
    const bot = selection.bot;
    setModelSaving(true);
    chat.reportStatus(null);
    try {
      await updateBotConfig(bot.id, { chat_model_id: nextModelId || null });
      if (!chat.ownsBot(bot)) return;
      await refreshBotState(bot);
      if (chat.ownsBot(bot)) {
        chat.reportStatus(nextModelId ? t("chat.status.chatModelUpdated") : t("chat.status.usingFallbackModel"));
      }
    } catch (err) {
      if (chat.ownsBot(bot)) chat.reportStatus(errorMessage(err));
    } finally {
      if (chat.ownsBot(bot)) setModelSaving(false);
    }
  }

  function toggleReasoning(messageId: string) {
    setReasoningOpenById((prev) => ({ ...prev, [messageId]: !prev[messageId] }));
  }

  function toggleToolInvocation(toolKey: string) {
    setToolOpenById((prev) => ({ ...prev, [toolKey]: !prev[toolKey] }));
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
          chat.chooseSession(item.session_id);
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
        onClick={() => void refreshSessions(selection.bot).catch((err) => {
          if (chat.ownsBot(selection.bot)) chat.reportStatus(errorMessage(err));
        })}
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
                  onClick={() => chat.chooseSession(item.session_id)}
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
                  onClick={() => chat.showOlderMessages()}
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
          {toolApprovalNotice ? (
            <p role="status" className="chat-column mb-3 break-words text-xs text-zinc-400">
              {toolApprovalNotice.kind === "stale"
                ? t("chat.approval.stale")
                : t(toolApprovalNotice.approved ? "chat.approval.autoApproved" : "chat.approval.autoDenied", { tool: toolApprovalNotice.toolName })}
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
