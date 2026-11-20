import type { ToolApprovalEvent } from "./types";

export function parseToolApprovalEvent(value: unknown): ToolApprovalEvent | "invalid" | null {
  if (
    typeof value !== "object" || value === null || !("type" in value) ||
    (value.type !== "tool_approval_request" && value.type !== "tool_approval_resolved")
  ) return null;

  if (
    !("approval_id" in value) || typeof value.approval_id !== "string" || !value.approval_id ||
    !("session_id" in value) || typeof value.session_id !== "string" || !value.session_id
  ) return "invalid";

  const scope = { approval_id: value.approval_id, session_id: value.session_id };
  if (value.type === "tool_approval_request") {
    if (
      !("tool_name" in value) || typeof value.tool_name !== "string" || !value.tool_name ||
      !("approval_actor" in value) || typeof value.approval_actor !== "string" ||
      !("approval_preview" in value) || typeof value.approval_preview !== "string"
    ) return "invalid";

    return {
      ...scope,
      type: value.type,
      tool_name: value.tool_name,
      approval_actor: value.approval_actor,
      approval_preview: value.approval_preview,
    };
  }

  if (
    !("approved" in value) || typeof value.approved !== "boolean" ||
    !("approval_source" in value) ||
    (value.approval_source !== "human" && value.approval_source !== "auto_review" && value.approval_source !== "policy")
  ) return "invalid";

  return {
    ...scope,
    type: value.type,
    approved: value.approved,
    approval_source: value.approval_source,
    tool_name: "tool_name" in value && typeof value.tool_name === "string" ? value.tool_name : undefined,
  };
}
