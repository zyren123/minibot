# Execution traces

MiniBot can write lightweight execution metadata to UTF-8 JSONL files. Recording is disabled by default.

## Configuration

The top-level `trace_dir` setting in the global `config/default.yaml` enables recording. A relative path resolves against the application home when configuration loads. Server bots inherit that resolved destination. An absolute path selects a destination directly.

```yaml
trace_dir: traces
```

The SDK and direct `Agent` entry point accept the same setting through `Config`.

```python
from pathlib import Path
from minibot import Minibot
from minibot.config import Config

bot = Minibot(config=Config(trace_dir=Path("traces")))
result = await bot.chat("Describe this project")
```

Set `trace_dir: null` to disable recording. There is no automatic retention or rotation.

## Records

Each execution segment owns an exclusively created `<run_id>.jsonl` file. Different sessions and processes use separate UUIDs and files. Every line is flushed before execution proceeds. There is no background buffer or shared append file.

All records contain `version`, `event`, `run_id`, `session_id`, `sequence`, and `elapsed_ms`. Version is `1`. Sequence numbers increase within the run. Elapsed times and durations use a monotonic clock in milliseconds. `run_start.started_at` is a UTC wall-clock timestamp.

| Event | Additional fields |
| --- | --- |
| `run_start` | `scope`, `parent_run_id`, `started_at` |
| `tool_start` | `invocation_id`, `tool_call_id`, `tool_name` |
| `tool_end` | Same invocation fields, `status`, `is_error`, `duration_ms`, optional `exit_code` and `hook_phase` |
| `tool_approval` | `approval_id`, `tool_name`, `approved`, `source` |
| `run_waiting` | None |
| `run_resumed` | None |
| `run_end` | `status`, `duration_ms`, optional `budget_limit` |

An internal invocation UUID pairs each tool start with its end, even when provider call IDs repeat. Tool statuses preserve `ToolOutcome` values. Cancellation produces an `interrupted` tool status and continues to raise the original interruption. A post-hook failure can retain tool status `success` while setting `is_error` and `hook_phase` to `post`.

Tool duration starts before delivery of `tool_call` and ends before delivery of `tool_result`. It includes pre-hooks, execution, post-hooks, owned cleanup, terminal rendering and event-delivery delays between those boundaries. Argument JSON parsing happens before the start. Successful ask-user tool execution ends before waiting for an answer, so human response time is excluded from tool duration.

## Lifecycle

Run end states are `completed`, `failed`, `interrupted`, `waiting_for_user`, and `budget_exceeded`. A budget stop records the configured limit name in `budget_limit`. See [Runtime budgets](runtime-budget.md). `completed` means the execution loop returned. It does not judge whether the user's task succeeded. Tool failures can occur inside a completed run. An `assistant_end` event does not close a run because more tools or model rounds can follow it.

A live question emits `run_waiting`. Its answer emits `run_resumed` in the same run. A persisted pause ends with `waiting_for_user`. The runtime sidecar stores `trace_run_id`; the server answer-stream route passes it into the resumed segment's `parent_run_id`, including after bot-cache eviction or restart. Existing sidecars without this field produce unlinked segments. These links do not provide checkpoint replay or deduplication of side effects. The SDK's `resume_pending_question_stream` continues prepared history, as before; persisting the answer remains the caller's responsibility outside the server route.

SDK streaming runs reuse the producer's ownership UUID. Detaching or closing an asynchronous reader leaves its producer and recorder alive. SDK cancellation and synchronous iterator shutdown retain existing producer cleanup. Terminal records follow owned tool finalizers. Run end describes Agent execution before subsequent SDK message persistence, so it is not a message-store durability acknowledgment.

## Scope and output policy

`scope` is `agent_tools`. Records cover tools in the owning Agent loop. A `Task` call is one invocation. Its subagent's internal tool calls are excluded. Team activity has no parent-child trace graph. Separately invoked Agents with tracing enabled create their own runs. Direct `Agent.run_loop`, SDK chat, streaming and synchronous wrappers all support recording.

[Tool approval](tool-approvals.md) decisions are recorded in the trace of the Agent
run that owns the decision, including decisions requested by a delegated Agent.
The trace does not record approval previews or reviewer prompts.

Records use an allowlist. They omit prompts, arguments, tool output, assistant text, reasoning, generic system payloads, exception messages and hook diagnostic strings. Session IDs, tool names and provider call IDs remain metadata in the file. Avoid putting credentials in those identifiers.

Open, write, flush and close failures produce a fixed warning through the `minibot.traces` Python logger. The affected recorder is disabled. Generation continues, and tracing does not swallow cancellation or trigger retries. An unavailable destination, process crash, or hard kill can leave a missing or incomplete trace. Consumers must require a terminal `run_end` before treating a file as complete. Flushing does not provide filesystem crash durability.

The contract tests read generated files and exercise failures, repeated IDs, concurrent sessions, pause and resume, consumer detachment, UTF-8 handling, write failures, and real Bash process cleanup.

```bash
uv run --with pytest --with pytest-asyncio pytest tests/test_run_traces.py tests/test_server_webui_bots.py -q
```

## Run the task regression checks

From the repository root, run the fixed tasks with a scripted model and temporary workspaces.

```bash
uv run --with pytest --with pytest-asyncio pytest -v tests/test_task_regression.py
```

The suite covers file creation and editing, pre-tool hook refusal, tool failure with a fallback,
cancellation during tool execution, and resuming a persisted question through a new server instance.
The scenarios run without model credentials, a Docker daemon, or a running web server.

For each task, pytest checks the trace steps and terminal status, the operation journal, and the final
files. A task can pass with a tool error when the scenario expects that error and the fallback works.
An unusable trace or a mismatch fails the task with a diagnostic and a nonzero exit status. The same
command runs probes that corrupt a captured trace or its effects and verify the corresponding failures.
The existing CI pytest step discovers this suite automatically.
