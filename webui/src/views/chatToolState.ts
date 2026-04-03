import type { Message, StreamEvent } from "../lib/types";

export type ToolInvocationStatus = "running" | "done" | "error";

export type ToolInvocationView = {
  key: string;
  toolCallId: string | null;
  name: string;
  argumentsText: string;
  outputText: string;
  status: ToolInvocationStatus;
};

export type ToolRenderState = {
  assistantInvocationsByIndex: Map<number, ToolInvocationView[]>;
  linkedToolMessageIndexes: Set<number>;
  orphanInvocationsByIndex: Map<number, ToolInvocationView>;
};

type ToolCallShape = NonNullable<Message["tool_calls"]>[number];

function toolCallName(toolCall: ToolCallShape): string {
  if ("function" in toolCall) {
    return toolCall.function?.name?.trim() || "";
  }
  return toolCall.name?.trim() || "";
}

function toolCallArguments(toolCall: ToolCallShape): string {
  if ("function" in toolCall) {
    return toolCall.function?.arguments ?? "";
  }
  return toolCall.arguments ?? "";
}

function isAskUserTool(name: string): boolean {
  return name.trim().toLowerCase() === "askuserquestion";
}

export function normalizeStreamToolCalls(
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

export function formatToolArguments(raw: string | Record<string, unknown> | null | undefined) {
  if (raw && typeof raw === "object") {
    return JSON.stringify(raw, null, 2);
  }
  const text = raw?.trim() ?? "";
  if (!text) return "";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function buildToolRenderState(messages: Message[]): ToolRenderState {
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

    const invocations = message.tool_calls
      .map((toolCall, toolIndex) => {
        const toolCallId = toolCall.id?.trim() || null;
        const toolName = toolCallName(toolCall);
        if (isAskUserTool(toolName)) return null;
        const result = toolCallId ? toolResultsByCallId.get(toolCallId) : undefined;
        if (result) linkedToolMessageIndexes.add(result.index);
        const status: ToolInvocationStatus = result ? (result.message.is_error ? "error" : "done") : "running";
        return {
          key: toolCallId ?? `${message.message_id ?? `assistant-${index}`}-tool-${toolIndex}`,
          toolCallId,
          name: toolName,
          argumentsText: formatToolArguments(toolCallArguments(toolCall)),
          outputText: result?.message.content ?? "",
          status,
        };
      })
      .filter((item): item is ToolInvocationView => Boolean(item));

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
      argumentsText: formatToolArguments(message.tool_args),
      outputText: message.content ?? "",
      status: message.is_error ? "error" : "done",
    });
  }

  return { assistantInvocationsByIndex, linkedToolMessageIndexes, orphanInvocationsByIndex };
}
