import type {
  ChatStreamEvent,
  Message,
  PendingQuestion,
  SessionMeta,
  StreamEvent,
  TodoSnapshot,
  ToolApprovalRequestEvent,
  Usage,
} from "../lib/types";
import { expandHistoryWindowStart, findHistoryWindowStart } from "./chatHistoryWindow.ts";
import { normalizeStreamToolCalls } from "./chatToolState.ts";

const HISTORY_TURNS = 24;

export type ChatBot = { readonly id: string };
export type ChatSelection = { readonly bot: ChatBot; readonly sessionId: string | null };
type SessionListLoad = { readonly bot: ChatBot };
type SessionCreation = { readonly bot: ChatBot };
type SavedLoad = { readonly selection: ChatSelection; readonly run?: ChatRun };

type SendRun = { readonly kind: "send"; readonly bot: ChatBot };
type AnswerRun = { readonly kind: "answer"; readonly bot: ChatBot; readonly sessionId: string; readonly questionId: string };
type RegenerateRun = {
  readonly kind: "regenerate";
  readonly bot: ChatBot;
  readonly sessionId: string;
  readonly messageId: string;
  readonly previousMessages: Message[];
};
export type ChatRun = SendRun | AnswerRun | RegenerateRun;

export type PendingToolApproval = ToolApprovalRequestEvent & { state: "pending" | "submitting" | "error" };
type ApprovalDecision = { readonly selection: ChatSelection; readonly run: ChatRun; readonly id: string };
type ToolApprovalNotice = { kind: "stale" } | { kind: "auto_review"; approved: boolean; toolName: string };

type ChatSnapshot = {
  selection: ChatSelection;
  sessions: SessionMeta[];
  messages: Message[];
  visibleHistoryStartIndex: number;
  contextBySession: Record<string, ContextSnapshot>;
  activeRun: { token: ChatRun; generating: boolean } | null;
  status: string | null;
  pendingQuestion: PendingQuestion | null;
  todoSnapshot: TodoSnapshot | null;
  toolApprovals: PendingToolApproval[];
  toolApprovalNotice: ToolApprovalNotice | null;
};

function emptySelection(bot: ChatBot, sessionId: string | null) {
  return {
    selection: { bot, sessionId },
    messages: [],
    visibleHistoryStartIndex: 0,
    pendingQuestion: null,
    todoSnapshot: null,
    toolApprovals: [],
    toolApprovalNotice: null,
  };
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

function normalizeUsage(raw: unknown): Usage | null {
  if (!raw || typeof raw !== "object") return null;
  const normalized: Usage = {};
  for (const key of ["prompt_tokens", "completion_tokens", "total_tokens"] as const) {
    const value = Object.getOwnPropertyDescriptor(raw, key)?.value;
    if (typeof value === "number" && Number.isFinite(value)) {
      normalized[key] = value;
    }
  }
  return Object.keys(normalized).length > 0 ? normalized : null;
}

export type ContextSnapshot = {
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

export function contextSnapshotFromMessages(messages: Message[]): ContextSnapshot | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const contextTokens = message.context_usage?.total_tokens;
    if (contextTokens != null) {
      return {
        totalTokens: contextTokens,
        compacted: true,
      };
    }
    const tokens = message.usage?.total_tokens;
    if (tokens != null) {
      return {
        totalTokens: tokens,
        compacted: false,
      };
    }
  }
  return null;
}

function patchAssistant(
  messages: Message[],
  event: ChatStreamEvent,
  update: (current: Message) => Partial<Message>,
): Message[] {
  let index = event.message_id ? messages.findIndex((item) => item.message_id === event.message_id) : -1;
  if (index < 0 && messages.at(-1)?.role === "assistant") index = messages.length - 1;
  const current: Message = index < 0 ? { role: "assistant", content: "", reasoning: "" } : messages[index];
  const next = [...messages];
  next[index < 0 ? next.length : index] = {
    ...current,
    message_id: event.message_id ?? current.message_id ?? null,
    parent_user_message_id: event.parent_user_message_id ?? current.parent_user_message_id ?? null,
    ...update(current),
  };
  return next;
}

function eventContext(usage: Usage | null, contextUsage: Usage | null, compacted?: boolean): ContextSnapshot | null {
  if (contextUsage?.total_tokens != null) return { totalTokens: contextUsage.total_tokens, compacted: Boolean(compacted) };
  return usage?.total_tokens != null ? { totalTokens: usage.total_tokens, compacted: false } : null;
}

function projectStreamEvent(snapshot: ChatSnapshot, run: ChatRun, sessionId: string, event: StreamEvent): Partial<ChatSnapshot> | null {
  const { messages, toolApprovals } = snapshot;
  switch (event.type) {
    case "model_generation_start":
    case "model_generation_end":
      return { activeRun: { token: run, generating: event.type === "model_generation_start" } };
    case "tool_approval_request":
      if (toolApprovals.some((approval) => approval.approval_id === event.approval_id)) return null;
      return { toolApprovals: [...toolApprovals, { ...event, state: "pending" }] };
    case "tool_approval_resolved":
      return {
        toolApprovals: toolApprovals.filter((approval) => approval.approval_id !== event.approval_id),
        toolApprovalNotice: event.approval_source === "auto_review" && event.tool_name
          ? { kind: "auto_review", approved: event.approved, toolName: event.tool_name }
          : snapshot.toolApprovalNotice,
      };
    case "todo_snapshot":
      return { todoSnapshot: normalizeTodoSnapshot(event.todo) };
    case "assistant_start":
      return { messages: patchAssistant(messages, event, (current) => ({
        completion_state: current.completion_state ?? "complete",
      })) };
    case "assistant_reasoning_delta":
      return { messages: patchAssistant(messages, event, (current) => ({
        completion_state: current.completion_state ?? "complete",
        reasoning: `${current.reasoning ?? ""}${event.reasoning_text ?? ""}`,
      })) };
    case "assistant_delta":
      return { messages: patchAssistant(messages, event, (current) => ({
        completion_state: current.completion_state ?? "complete",
        content: `${current.content ?? ""}${event.delta_text ?? ""}`,
      })) };
    case "assistant_end":
      return { messages: patchAssistant(messages, event, (current) => ({
        completion_state: "complete",
        content: typeof event.content === "string" ? event.content : current.content,
        reasoning: typeof event.reasoning === "string" ? event.reasoning : current.reasoning,
        usage: event.usage ?? current.usage ?? null,
        tool_calls: event.tool_calls ? normalizeStreamToolCalls(event.tool_calls) : current.tool_calls ?? [],
      })) };
    case "ask_user_question": {
      const pendingQuestion = derivePendingQuestionFromPrompt({
        question_id: event.question_id ?? "",
        message_id: event.message_id ?? null,
        prompt: event.prompt ?? "",
        options: event.options ?? [],
        allow_free_text: Boolean(event.allow_free_text),
        required: Boolean(event.required),
      });
      const usage = normalizeUsage(event.usage);
      const contextUsage = normalizeUsage(event.context_usage);
      const context = eventContext(usage, contextUsage, event.context_compacted);
      return {
        pendingQuestion,
        messages: patchAssistant(messages, event, (current) => ({
          content: current.content?.trim() ? current.content : pendingQuestion?.prompt ?? event.prompt ?? "",
          usage: usage ?? current.usage ?? null,
          context_usage: contextUsage ?? current.context_usage ?? null,
        })),
        ...(context && { contextBySession: { ...snapshot.contextBySession, [sessionId]: context } }),
      };
    }
    case "ask_user_answer_received":
      return {
        pendingQuestion: null,
        messages: run.kind === "answer" ? messages : [...messages, { role: "user", content: event.answer_text ?? "" }],
      };
    case "tool_result":
      return { messages: [...messages, {
        role: "tool",
        content: event.tool_output ?? "",
        tool_call_id: event.tool_call_id,
        tool_name: event.tool_name ?? null,
        tool_args: event.tool_args ?? null,
        is_error: event.is_error ?? null,
        tool_status: event.tool_status,
        tool_exit_code: event.tool_exit_code,
        tool_artifact_id: event.tool_artifact_id,
        tool_hook_error: event.tool_hook_error,
        tool_hook_phase: event.tool_hook_phase,
      }] };
    case "system": {
      if (typeof event.message !== "string" || event.message === "session_created") return null;
      const context = eventContext(normalizeUsage(event.data?.usage), normalizeUsage(event.data?.context_usage), event.data?.context_compacted);
      return {
        status: event.message,
        ...(context && { contextBySession: { ...snapshot.contextBySession, [sessionId]: context } }),
      };
    }
    default:
      return null;
  }
}

export class ChatSessionState {
  #snapshot: ChatSnapshot;
  #savedLoad: SavedLoad | null = null;
  #listLoad: SessionListLoad | null = null;
  #creation: SessionCreation | null = null;
  #listeners = new Set<() => void>();

  constructor(botId: string) {
    this.#snapshot = {
      ...emptySelection({ id: botId }, null),
      sessions: [],
      contextBySession: {},
      activeRun: null,
      status: null,
    };
  }

  getSnapshot = (): ChatSnapshot => this.#snapshot;

  subscribe = (listener: () => void) => {
    this.#listeners.add(listener);
    return () => { this.#listeners.delete(listener); };
  };

  #update(changes: Partial<ChatSnapshot>) {
    this.#snapshot = { ...this.#snapshot, ...changes };
    this.#listeners.forEach((listener) => listener());
  }

  selectBot(botId: string): ChatBot {
    if (this.#snapshot.selection.bot.id === botId) return this.#snapshot.selection.bot;
    const bot = { id: botId };
    this.#savedLoad = null;
    this.#listLoad = null;
    this.#creation = null;
    this.#update({
      ...emptySelection(bot, null),
      sessions: [],
      contextBySession: {},
      activeRun: null,
      status: null,
    });
    return bot;
  }

  ownsBot(bot: ChatBot) {
    return this.#snapshot.selection.bot === bot;
  }

  ownsSelection(selection: ChatSelection) {
    return this.#snapshot.selection === selection;
  }

  beginSessionListLoad(bot: ChatBot): SessionListLoad | null {
    if (!this.ownsBot(bot)) return null;
    const load = { bot };
    this.#listLoad = load;
    return load;
  }

  ownsSessionListLoad(load: SessionListLoad) {
    return this.#listLoad === load;
  }

  acceptSessionList(load: SessionListLoad, sessions: SessionMeta[]) {
    const { selection, activeRun } = this.#snapshot;
    if (!this.ownsSessionListLoad(load)) return;
    this.#listLoad = null;
    const selected = selection.sessionId;
    const next = activeRun || (selected && sessions.some((session) => session.session_id === selected))
      ? selected : sessions[0]?.session_id ?? null;
    if (next !== selected) {
      this.#savedLoad = null;
      this.#update({ ...emptySelection(load.bot, next), sessions });
    } else {
      this.#update({ sessions });
    }
  }

  chooseSession(sessionId: string) {
    const { selection, activeRun } = this.#snapshot;
    if (activeRun) return;
    this.#creation = null;
    if (selection.sessionId === sessionId) return;
    this.#savedLoad = null;
    this.#update(emptySelection(selection.bot, sessionId));
  }

  beginSessionCreation(bot: ChatBot): SessionCreation | null {
    if (!this.ownsBot(bot) || this.#snapshot.activeRun) return null;
    const creation = { bot };
    this.#creation = creation;
    return creation;
  }

  finishSessionCreation(creation: SessionCreation): boolean {
    if (this.#creation !== creation) return false;
    this.#creation = null;
    return true;
  }

  activateCreatedSession(creation: SessionCreation, sessionId: string): boolean {
    if (!this.finishSessionCreation(creation)) return false;
    this.#listLoad = null;
    this.chooseSession(sessionId);
    return true;
  }

  bindSendSession(run: SendRun, sessionId: string): boolean {
    if (!this.ownsRun(run) || this.#snapshot.selection.sessionId !== null) return false;
    this.#savedLoad = null;
    this.#update(emptySelection(run.bot, sessionId));
    return true;
  }

  beginLoad(selection: ChatSelection, run?: ChatRun): SavedLoad | null {
    if (!this.ownsSelection(selection) || !selection.sessionId || (run && !this.ownsRun(run))) return null;
    const load = { selection, run };
    this.#savedLoad = load;
    return load;
  }

  ownsLoad(load: SavedLoad) {
    return this.#savedLoad === load && this.ownsSelection(load.selection) && (!load.run || this.ownsRun(load.run));
  }

  acceptSaved(load: SavedLoad | null, messages: Message[], question: PendingQuestion | null): boolean {
    if (!load || !this.ownsLoad(load)) return false;
    this.#replaceHistory(messages, {
      pendingQuestion: derivePendingQuestionFromPrompt(question),
      todoSnapshot: null,
    });
    return true;
  }

  replaceDeletedTurn(selection: ChatSelection, messages: Message[]): boolean {
    if (!this.ownsSelection(selection) || this.#snapshot.activeRun) return false;
    this.#replaceHistory(messages);
    return true;
  }

  removeSession(bot: ChatBot, sessionId: string) {
    if (!this.ownsBot(bot)) return;
    const contextBySession = { ...this.#snapshot.contextBySession };
    delete contextBySession[sessionId];
    if (this.#snapshot.selection.sessionId === sessionId) {
      this.#savedLoad = null;
      this.#update({ ...emptySelection(bot, null), activeRun: null, contextBySession });
    } else {
      this.#update({ contextBySession });
    }
  }

  #history(messages: Message[]): Partial<ChatSnapshot> {
    const contextBySession = { ...this.#snapshot.contextBySession };
    const id = this.#snapshot.selection.sessionId;
    if (id) {
      const context = contextSnapshotFromMessages(messages);
      if (context) contextBySession[id] = context;
      else delete contextBySession[id];
    }
    return {
      messages,
      contextBySession,
      visibleHistoryStartIndex: findHistoryWindowStart(messages, HISTORY_TURNS),
    };
  }

  #replaceHistory(messages: Message[], changes: Partial<ChatSnapshot> = {}) {
    this.#savedLoad = null;
    this.#update({ ...this.#history(messages), ...changes });
  }

  showOlderMessages() {
    const { messages, visibleHistoryStartIndex } = this.#snapshot;
    this.#update({ visibleHistoryStartIndex: expandHistoryWindowStart(messages, visibleHistoryStartIndex, HISTORY_TURNS) });
  }

  reportStatus(status: string | null) {
    if (status !== this.#snapshot.status) this.#update({ status });
  }

  beginSend(bot: ChatBot): SendRun | null {
    if (!this.ownsBot(bot) || this.#snapshot.activeRun || this.#snapshot.pendingQuestion) return null;
    const run: SendRun = { kind: "send", bot };
    this.#beginRun(run);
    return run;
  }

  recordSend(run: SendRun, text: string): boolean {
    if (!this.ownsRun(run) || !this.#snapshot.selection.sessionId) return false;
    this.#savedLoad = null;
    this.#update({ messages: [...this.#snapshot.messages, { role: "user", content: text }] });
    return true;
  }

  beginAnswer(bot: ChatBot, text: string): AnswerRun | null {
    const { activeRun, pendingQuestion, selection, messages } = this.#snapshot;
    const sessionId = selection.sessionId;
    if (!this.ownsBot(bot) || !sessionId || !pendingQuestion || activeRun?.token.kind === "answer") return null;
    const run: AnswerRun = { kind: "answer", bot, sessionId, questionId: pendingQuestion.question_id };
    this.#beginRun(run, {
      pendingQuestion: null,
      messages: [...messages, { role: "user", content: text }],
    });
    return run;
  }

  beginRegenerate(bot: ChatBot, messageId: string): RegenerateRun | null {
    const { activeRun, selection, messages } = this.#snapshot;
    const sessionId = selection.sessionId;
    if (!this.ownsBot(bot) || !sessionId || activeRun) return null;
    const bounds = findAssistantTurnBounds(messages, messageId);
    const run: RegenerateRun = { kind: "regenerate", bot, sessionId, messageId, previousMessages: messages };
    const remaining = bounds
      ? [...messages.slice(0, bounds.userIndex + 1), ...messages.slice(bounds.turnEnd)] : messages;
    this.#beginRun(run, this.#history(remaining));
    return run;
  }

  rollbackRegeneration(run: RegenerateRun) {
    if (this.ownsRun(run)) this.#replaceHistory(run.previousMessages);
  }

  #beginRun(run: ChatRun, changes: Partial<ChatSnapshot> = {}) {
    this.#savedLoad = null;
    this.#creation = null;
    this.#update({
      activeRun: { token: run, generating: false },
      todoSnapshot: null,
      toolApprovals: [],
      toolApprovalNotice: null,
      status: null,
      ...changes,
    });
  }

  ownsRun(run: ChatRun) {
    return this.#snapshot.activeRun?.token === run;
  }

  finishRun(run: ChatRun) {
    if (this.ownsRun(run)) this.#update({ activeRun: null, toolApprovals: [] });
  }

  endStreaming(run: ChatRun, sessionId: string) {
    if (this.ownsRun(run) && this.#snapshot.selection.sessionId === sessionId) {
      this.#update({ activeRun: { token: run, generating: false }, toolApprovals: [] });
    }
  }

  applyStreamEvent(run: ChatRun, sessionId: string, event: StreamEvent): boolean {
    if (!this.ownsRun(run) || this.#snapshot.selection.sessionId !== sessionId) return false;
    if (event.session_id && event.session_id !== sessionId) return false;
    const changes = projectStreamEvent(this.#snapshot, run, sessionId, event);
    if (!changes) return false;
    this.#savedLoad = null;
    this.#update(changes);
    return true;
  }

  beginApproval(approval: PendingToolApproval): ApprovalDecision | null {
    const { selection, activeRun, toolApprovals } = this.#snapshot;
    if (!activeRun || selection.sessionId !== approval.session_id ||
        !toolApprovals.some((item) => item === approval && item.state !== "submitting")) return null;
    this.#update({
      toolApprovals: toolApprovals.map((item) => item === approval ? { ...item, state: "submitting" } : item),
    });
    return { selection, run: activeRun.token, id: approval.approval_id };
  }

  finishApproval(decision: ApprovalDecision, result: "accepted" | "stale" | "error") {
    if (!this.ownsSelection(decision.selection) || !this.ownsRun(decision.run)) return;
    const { toolApprovals, toolApprovalNotice } = this.#snapshot;
    this.#update({
      toolApprovals: result === "error"
        ? toolApprovals.map((item) => item.approval_id === decision.id ? { ...item, state: "error" } : item)
        : toolApprovals.filter((item) => item.approval_id !== decision.id),
      toolApprovalNotice: result === "stale" ? { kind: "stale" } : toolApprovalNotice,
    });
  }
}
