import assert from "node:assert/strict";
import test from "node:test";

import {
  countHiddenTurnsBeforeIndex,
  expandHistoryWindowStart,
  findHistoryWindowStart,
} from "../src/views/chatHistoryWindow.ts";

function buildMessages() {
  return [
    { role: "user", content: "u1" },
    { role: "assistant", content: "a1" },
    { role: "tool", content: "t1" },
    { role: "user", content: "u2" },
    { role: "assistant", content: "a2" },
    { role: "user", content: "u3" },
    { role: "assistant", content: "a3" },
    { role: "tool", content: "t3" },
    { role: "user", content: "u4" },
    { role: "assistant", content: "a4" },
  ];
}

test("findHistoryWindowStart starts on a full user turn boundary", () => {
  const messages = buildMessages();

  assert.equal(findHistoryWindowStart(messages, 2), 5);
});

test("expandHistoryWindowStart reveals earlier full turns in batches", () => {
  const messages = buildMessages();
  const initialStart = findHistoryWindowStart(messages, 2);

  assert.equal(countHiddenTurnsBeforeIndex(messages, initialStart), 2);
  assert.equal(expandHistoryWindowStart(messages, initialStart, 1), 3);
  assert.equal(expandHistoryWindowStart(messages, 3, 2), 0);
});
