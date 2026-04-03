import assert from "node:assert/strict";
import test from "node:test";

import { buildToolRenderState } from "../src/views/chatToolState.ts";

test("buildToolRenderState reads persisted assistant tool call summaries", () => {
  const state = buildToolRenderState([
    {
      role: "assistant",
      content: "",
      message_id: "msg-assistant-1",
      tool_calls: [
        {
          id: "call-1",
          name: "create_memory",
          arguments: "{\"title\":\"荷月\"}",
        },
      ],
    },
    {
      role: "tool",
      content: "Created memory://characters/heyue",
      tool_call_id: "call-1",
      tool_name: "create_memory",
      tool_args: { title: "荷月" },
    },
  ]);

  const invocation = state.assistantInvocationsByIndex.get(0)?.[0];
  assert.ok(invocation);
  assert.equal(invocation.name, "create_memory");
  assert.match(invocation.argumentsText, /"title": "荷月"/);
  assert.equal(invocation.outputText, "Created memory://characters/heyue");
  assert.equal(invocation.status, "done");
});

test("buildToolRenderState hides askuserquestion tool invocations", () => {
  const state = buildToolRenderState([
    {
      role: "assistant",
      content: "Which task should I handle first?",
      message_id: "msg-assistant-1",
      tool_calls: [
        {
          id: "ask-call-1",
          name: "askuserquestion",
          arguments: "{\"prompt\":\"Which task should I handle first?\"}",
        },
      ],
    },
  ]);

  assert.equal(state.assistantInvocationsByIndex.size, 0);
});

test("buildToolRenderState shows orphan tool arguments from persisted tool messages", () => {
  const state = buildToolRenderState([
    {
      role: "tool",
      content: "Created memory://characters/heyue",
      tool_call_id: "call-2",
      tool_name: "create_memory",
      tool_args: { title: "荷月", kind: "memory" },
    },
  ]);

  const invocation = state.orphanInvocationsByIndex.get(0);
  assert.ok(invocation);
  assert.equal(invocation.name, "create_memory");
  assert.match(invocation.argumentsText, /"kind": "memory"/);
  assert.equal(invocation.status, "done");
});
