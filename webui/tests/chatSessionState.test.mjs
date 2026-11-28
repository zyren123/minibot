import assert from "node:assert/strict";
import test from "node:test";

import { ChatSessionState } from "../src/views/chatSessionState.ts";

function session() {
  const chat = new ChatSessionState("writer");
  chat.chooseSession("draft");
  return chat;
}

test("old loads cannot overwrite a reselected session or a newer load", () => {
  const chat = session();
  const original = chat.getSnapshot().selection;
  chat.acceptSaved(chat.beginLoad(original), [{ role: "user", content: "Original transcript" }], null);
  const first = chat.beginLoad(original);
  chat.chooseSession("notes");
  assert.deepEqual(chat.getSnapshot().messages, []);
  chat.chooseSession("draft");
  const second = chat.beginLoad(chat.getSnapshot().selection);
  const latest = chat.beginLoad(chat.getSnapshot().selection);

  assert.equal(chat.acceptSaved(first, [{ role: "user", content: "Old visit" }], null), false);
  assert.equal(chat.acceptSaved(latest, [{ role: "user", content: "Latest transcript" }], null), true);
  assert.equal(chat.acceptSaved(second, [{ role: "user", content: "Earlier load" }], null), false);
  assert.equal(chat.getSnapshot().messages[0].content, "Latest transcript");
});

test("live content invalidates a saved load and survives stop until saved interruption arrives", () => {
  const chat = session();
  const selection = chat.getSnapshot().selection;
  const run = chat.beginSend(selection.bot);
  chat.recordSend(run, "Write");
  const stale = chat.beginLoad(selection);
  chat.applyStreamEvent(run, "draft", { type: "model_generation_start" });
  chat.applyStreamEvent(run, "draft", { type: "assistant_delta", message_id: "answer", delta_text: "还未写完" });

  assert.equal(chat.acceptSaved(stale, [], null), false);
  chat.endStreaming(run, "draft");
  assert.equal(chat.getSnapshot().messages.at(-1).content, "还未写完");

  const saved = chat.beginLoad(selection, run);
  const partial = { ...chat.getSnapshot().messages.at(-1), completion_state: "interrupted" };
  assert.equal(chat.acceptSaved(saved, [{ role: "user", content: "Write" }, partial], null), true);
  chat.finishRun(run);
  assert.equal(chat.getSnapshot().messages.at(-1).completion_state, "interrupted");
});

test("an answer supersedes its send stream and appends the answer once", () => {
  const chat = session();
  const bot = chat.getSnapshot().selection.bot;
  const send = chat.beginSend(bot);
  chat.recordSend(send, "Write");
  chat.applyStreamEvent(send, "draft", {
    type: "ask_user_question", message_id: "q-message", question_id: "q", prompt: "Which ending?",
    options: [], allow_free_text: true, usage: { total_tokens: 100 },
    context_usage: { total_tokens: 40 }, context_compacted: true,
  });
  assert.equal(chat.getSnapshot().pendingQuestion.question_id, "q");
  assert.deepEqual(chat.getSnapshot().messages.at(-1).usage, { total_tokens: 100 });
  assert.deepEqual(chat.getSnapshot().messages.at(-1).context_usage, { total_tokens: 40 });
  assert.deepEqual(chat.getSnapshot().contextBySession.draft, { totalTokens: 40, compacted: true });

  const answer = chat.beginAnswer(bot, "Hopeful");
  assert.equal(answer.questionId, "q");
  chat.applyStreamEvent(answer, "draft", { type: "model_generation_start" });
  chat.endStreaming(send, "draft");
  chat.finishRun(send);
  chat.applyStreamEvent(send, "draft", { type: "assistant_delta", delta_text: "Late send text" });
  assert.equal(chat.getSnapshot().activeRun.token, answer);
  assert.equal(chat.getSnapshot().activeRun.generating, true);
  chat.applyStreamEvent(answer, "draft", { type: "ask_user_answer_received", answer_text: "Hopeful" });

  assert.deepEqual(chat.getSnapshot().messages.map(({ content }) => content), ["Write", "Which ending?", "Hopeful"]);
});

test("a saved pending question restores an answer retry", () => {
  const chat = session();
  const selection = chat.getSnapshot().selection;
  const question = { question_id: "q", prompt: "Continue?", options: [], allow_free_text: true, required: true };
  const messages = [{ role: "assistant", content: "Continue?" }];
  chat.acceptSaved(chat.beginLoad(selection), messages, question);
  const failed = chat.beginAnswer(selection.bot, "Yes");

  assert.equal(chat.getSnapshot().pendingQuestion, null);
  assert.equal(chat.acceptSaved(chat.beginLoad(selection, failed), messages, question), true);
  chat.finishRun(failed);
  assert.equal(chat.getSnapshot().pendingQuestion.question_id, "q");
  assert.equal(chat.beginAnswer(selection.bot, "Yes").questionId, "q");
  assert.deepEqual(chat.getSnapshot().messages.map(({ content }) => content), ["Continue?", "Yes"]);
});

test("Bot visits and declared stream sessions isolate late messages and approvals", () => {
  const chat = session();
  const first = chat.getSnapshot().selection;
  const send = chat.beginSend(first.bot);
  chat.recordSend(send, "Old Bot visit");
  chat.selectBot("editor");
  chat.selectBot("writer");
  chat.chooseSession("draft");
  const latest = chat.getSnapshot().selection;
  const active = chat.beginSend(latest.bot);
  chat.recordSend(active, "New visit");
  const approval = {
    type: "tool_approval_request", session_id: "draft", approval_id: "approval",
    tool_name: "write_file", approval_actor: "user", approval_preview: "file",
  };

  assert.equal(chat.applyStreamEvent(send, "draft", approval), false);
  assert.equal(chat.applyStreamEvent(active, "draft", { ...approval, session_id: "notes" }), false);
  assert.equal(chat.applyStreamEvent(active, "draft", approval), true);
  chat.endStreaming(send, "draft");
  assert.equal(chat.getSnapshot().toolApprovals.length, 1);
  chat.applyStreamEvent(active, "draft", { type: "assistant_delta", delta_text: "Current" });
  assert.deepEqual(chat.getSnapshot().messages.map(({ content }) => content), ["New visit", "Current"]);
});

test("regeneration removes the assistant turn and rollback preserves the original transcript", () => {
  const chat = session();
  const selection = chat.getSnapshot().selection;
  const previous = [
    { role: "user", content: "Write", message_id: "u" },
    { role: "assistant", content: "Draft", message_id: "a", parent_user_message_id: "u", usage: { total_tokens: 90 } },
    { role: "tool", content: "Output", tool_call_id: "t" },
  ];
  chat.acceptSaved(chat.beginLoad(selection), previous, null);
  const run = chat.beginRegenerate(selection.bot, "a");

  assert.deepEqual(chat.getSnapshot().messages, previous.slice(0, 1));
  chat.applyStreamEvent(run, "draft", { type: "assistant_delta", delta_text: "Retry" });
  chat.rollbackRegeneration(run);
  assert.deepEqual(chat.getSnapshot().messages, previous);
  assert.equal(chat.getSnapshot().contextBySession.draft.totalTokens, 90);
});

test("stream events keep plan, reasoning, tools, and approval decisions in the selected chat", () => {
  const chat = session();
  const selection = chat.getSnapshot().selection;
  const run = chat.beginSend(selection.bot);
  chat.recordSend(run, "Write");
  const events = [
    { type: "todo_snapshot", todo: { title: "Write a draft", items: [{ id: "outline", label: "Outline", status: "done" }], visible: true } },
    { type: "assistant_start", message_id: "assistant", parent_user_message_id: "user" },
    { type: "assistant_reasoning_delta", message_id: "assistant", reasoning_text: "Planning" },
    { type: "assistant_delta", message_id: "assistant", delta_text: "A draft" },
    { type: "assistant_end", message_id: "assistant", usage: { total_tokens: 29 }, tool_calls: [
      { id: "tool-call", name: "write_file", arguments: "{}" },
    ] },
    { type: "tool_result", tool_call_id: "tool-call", tool_name: "write_file", tool_output: "Saved it", tool_status: "success" },
    { type: "tool_approval_request", session_id: "draft", approval_id: "approval", tool_name: "write_file", approval_actor: "user", approval_preview: "draft" },
  ];
  for (const event of events) chat.applyStreamEvent(run, "draft", event);

  const { messages, todoSnapshot, toolApprovals } = chat.getSnapshot();
  assert.deepEqual(messages.slice(1).map(({ role, content }) => [role, content]), [
    ["assistant", "A draft"], ["tool", "Saved it"],
  ]);
  assert.equal(messages[1].reasoning, "Planning");
  assert.equal(messages[1].usage.total_tokens, 29);
  assert.equal(messages[1].tool_calls[0].id, messages[2].tool_call_id);
  assert.equal(todoSnapshot.completed, 1);
  assert.equal(todoSnapshot.items[0].label, "Outline");

  const approval = toolApprovals[0];
  const target = chat.beginApproval(approval);
  assert.equal(chat.getSnapshot().toolApprovals[0].state, "submitting");
  chat.finishApproval(target, "error");
  assert.equal(chat.getSnapshot().toolApprovals[0].state, "error");
  chat.applyStreamEvent(run, "draft", {
    type: "tool_approval_resolved", session_id: "draft", approval_id: "approval",
    tool_name: "write_file", approved: true, approval_source: "auto_review",
  });
  assert.equal(chat.getSnapshot().toolApprovals.length, 0);
  assert.deepEqual(chat.getSnapshot().toolApprovalNotice, { kind: "auto_review", approved: true, toolName: "write_file" });
});

function sessionEntry(id) {
  return { session_id: id, path: id, created_at: "", modified_at: "", message_count: 0, preview: "" };
}

test("an initial session list cannot take ownership from a requested new chat", () => {
  const chat = new ChatSessionState("writer");
  const bot = chat.getSnapshot().selection.bot;
  const initial = chat.beginSessionListLoad(bot);
  const creation = chat.beginSessionCreation(bot);
  chat.acceptSessionList(initial, [sessionEntry("older")]);

  assert.equal(chat.getSnapshot().selection.sessionId, "older");
  assert.equal(chat.activateCreatedSession(creation, "new"), true);
  assert.equal(chat.getSnapshot().selection.sessionId, "new");

  const listBeforeCreation = chat.beginSessionListLoad(bot);
  const second = chat.beginSessionCreation(bot);
  assert.equal(chat.activateCreatedSession(second, "second"), true);
  const refreshed = chat.beginSessionListLoad(bot);
  chat.acceptSessionList(refreshed, [sessionEntry("new"), sessionEntry("second")]);
  chat.acceptSessionList(listBeforeCreation, [sessionEntry("older")]);
  assert.deepEqual(chat.getSnapshot().sessions.map((item) => item.session_id), ["new", "second"]);
  assert.equal(chat.getSnapshot().selection.sessionId, "second");
});

test("explicit navigation, a newer creation, and a later Bot visit supersede an older creation", () => {
  const chat = session();
  const bot = chat.getSnapshot().selection.bot;
  const old = chat.beginSessionCreation(bot);
  chat.chooseSession("notes");
  assert.equal(chat.activateCreatedSession(old, "new"), false);
  const refreshed = chat.beginSessionListLoad(bot);
  chat.acceptSessionList(refreshed, [sessionEntry("new"), sessionEntry("notes")]);
  assert.equal(chat.getSnapshot().selection.sessionId, "notes");

  const first = chat.beginSessionCreation(bot);
  const latest = chat.beginSessionCreation(bot);
  assert.equal(chat.activateCreatedSession(first, "old request"), false);
  assert.equal(chat.activateCreatedSession(latest, "latest request"), true);

  const pending = chat.beginSessionCreation(bot);
  chat.selectBot("editor");
  chat.selectBot("writer");
  assert.equal(chat.activateCreatedSession(pending, "previous visit"), false);
  assert.equal(chat.getSnapshot().selection.sessionId, null);
});

test("a late approval response cannot replace the next turn's notice in the same session", () => {
  const chat = session();
  const bot = chat.getSnapshot().selection.bot;
  const first = chat.beginSend(bot);
  chat.applyStreamEvent(first, "draft", {
    type: "tool_approval_request", session_id: "draft", approval_id: "first",
    tool_name: "write_file", approval_actor: "user", approval_preview: "draft",
  });
  const decision = chat.beginApproval(chat.getSnapshot().toolApprovals[0]);
  chat.finishRun(first);

  const next = chat.beginSend(bot);
  chat.applyStreamEvent(next, "draft", {
    type: "tool_approval_resolved", session_id: "draft", approval_id: "next",
    tool_name: "write_file", approved: false, approval_source: "auto_review",
  });
  chat.finishApproval(decision, "stale");
  assert.deepEqual(chat.getSnapshot().toolApprovalNotice, { kind: "auto_review", approved: false, toolName: "write_file" });
});
