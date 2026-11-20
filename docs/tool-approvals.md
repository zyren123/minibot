# Tool permissions and approval

Set `tool_permissions` in the global `config/default.yaml` under the MiniBot app
home. Omitted settings retain `mode: "off"`, which preserves existing behavior.
Restart the REPL or WebUI server after updating the file.

## Ask for human approval

Set the mode to `manual` and assign `allow`, `deny`, or `ask` to tool names. The
`default` rule covers names you do not list.

```yaml
tool_permissions:
  mode: manual
  default: ask
  rules:
    read_file: allow
    askuserquestion: allow
    write_file: ask
    edit_file: ask
    bash: deny
    Task: allow
  timeout: 120
```

The REPL and WebUI ask before execution. Review the tool name, requesting Agent,
and arguments, then approve or deny that call. A new call requires its own
decision. A denied call stops before any pre-tool hook or tool body runs. An
approved call still goes through the existing pre-tool hooks and tool checks.

The dispatch rule applies to model requests from the main Agent, built-in
Subagents, Team workers, and WebUI-attached bots. Existing Subagent tool
allowlists still apply. File tools accept workspace-relative paths or absolute
paths within the workspace. MiniBot resolves and verifies the target before
approval and again before execution.

WebUI and streaming SDK approvals belong to a live run. A timeout, cancellation,
ended stream, or restart prevents pending approvals from being reused. The
timeout is in seconds and also counts against the active runtime budget. An
interaction without an approver, such as a non-streaming SDK call or a Team
worker without an active owner, refuses calls that require human approval.
Always allow tools you intend an unattended run to use.

## Enable AI review

Set `mode: auto_review` and provide workspace-relative paths. An `ask` rule can
then use the run's configured model to review a built-in write or edit within
those paths. Patterns match values such as `notes/report.md` after path
resolution. Hidden path components are excluded from AI review.

```yaml
tool_permissions:
  mode: auto_review
  default: ask
  rules:
    read_file: allow
    askuserquestion: allow
    write_file: ask
    edit_file: ask
    bash: deny
    Task: allow
  auto_review_paths:
    - "notes/**"
  timeout: 120
```

The reviewer gets a separate prompt with no tools. Its input includes the
user's request and the proposed file-tool arguments within a 4,000-character
limit. The review uses the run's configured model provider and counts against
the model and token budget. Unclear, invalid, unavailable, or out-of-scope
reviews go to the human handler. AI review grants no permission to Bash,
MCP, or custom tool calls. Explicit `deny` rules apply before any review.

## Use approvals from the SDK

Pass `ToolPermissionsConfig` on `Config`. For non-streaming SDK calls that need
human input, pass an async `tool_approval_handler` to `Minibot`. The handler
receives a `ToolApprovalRequest` with the exact invocation preview and returns
`True` to approve once. An async stream can instead handle
`tool_approval_request` and submit the decision on the same bot.

```python
async for event in bot.stream("Update notes/report.md"):
    if event["type"] == "tool_approval_request":
        approved = await your_confirmation_ui(event)
        bot.submit_tool_approval(event["approval_id"], approved)
```

A WebUI API client can resolve a live request through
`POST /api/bots/{bot_id}/sessions/{session_id}/tool-approvals/{approval_id}`
with a JSON boolean field `approved`. A stale, consumed, or mismatched ID
returns HTTP 409. Decisions are separate from persisted ask-user answers.

When execution tracing is enabled, JSONL records the approval ID, tool name,
decision and source. Approval previews and reviewer prompts stay out of trace
files. This policy authorizes model-requested tool dispatch. Trusted Python
plugins, hooks, and directly invoked Python functions retain their host access.
