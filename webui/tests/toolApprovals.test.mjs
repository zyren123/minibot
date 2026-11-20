import assert from "node:assert/strict";
import test from "node:test";

import { parseToolApprovalEvent } from "../src/lib/toolApprovals.ts";

test("keeps an approval bound to its session and displays its literal preview", () => {
  const event = {
    type: "tool_approval_request",
    session_id: "session-a",
    approval_id: "request-a",
    tool_name: "write_file",
    approval_actor: "subagent",
    approval_preview: '{ "content": "<strong>plain text</strong>" }',
  };
  assert.deepEqual(parseToolApprovalEvent(event), event);
  assert.equal(parseToolApprovalEvent({ ...event, session_id: "" }), "invalid");
  assert.equal(parseToolApprovalEvent({ ...event, approval_preview: 12 }), "invalid");
});

test("requires an explicit Boolean decision and known approval source", () => {
  const event = {
    type: "tool_approval_resolved",
    session_id: "session-a",
    approval_id: "request-a",
    approved: false,
    approval_source: "auto_review",
    tool_name: "write_file",
  };
  assert.deepEqual(parseToolApprovalEvent(event), event);
  assert.equal(parseToolApprovalEvent({ ...event, approved: "false" }), "invalid");
  assert.equal(parseToolApprovalEvent({ ...event, approval_source: "unknown" }), "invalid");
  assert.equal(parseToolApprovalEvent({ ...event, type: "system" }), null);
});
