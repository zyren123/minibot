import type { Message } from "../lib/types";

type MessageLike = Pick<Message, "role">;

export function findHistoryWindowStart(messages: MessageLike[], visibleTurnCount: number) {
  if (messages.length === 0 || visibleTurnCount <= 0) return 0;

  let remainingTurns = visibleTurnCount;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role !== "user") continue;
    remainingTurns -= 1;
    if (remainingTurns === 0) return index;
  }

  return 0;
}

export function countHiddenTurnsBeforeIndex(messages: MessageLike[], startIndex: number) {
  let count = 0;
  const end = Math.max(0, Math.min(startIndex, messages.length));
  for (let index = 0; index < end; index += 1) {
    if (messages[index].role === "user") count += 1;
  }
  return count;
}

export function expandHistoryWindowStart(messages: MessageLike[], currentStart: number, additionalTurnCount: number) {
  if (currentStart <= 0 || additionalTurnCount <= 0) return 0;

  let turnsToReveal = additionalTurnCount;
  for (let index = currentStart - 1; index >= 0; index -= 1) {
    if (messages[index].role !== "user") continue;
    turnsToReveal -= 1;
    if (turnsToReveal === 0) return index;
  }

  return 0;
}
