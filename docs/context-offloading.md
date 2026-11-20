# Context offloading

MiniBot saves large text tool results as local artifacts. The tool result
contains an opaque artifact ID and, when space permits, a short preview. The
agent can search the artifact and read a specific range without sending the
full output to the model. Bash, file reads, MCP tools, and custom text tools
use the same offloading rules.

## Configuration

The default settings in the app home at `config/default.yaml` are shown below.

```yaml
llm:
  max_tool_output_chars: 16000
context_offloading:
  enabled: true
  threshold_chars: null
  max_artifact_bytes: 8388608
```

- `enabled` defaults to `true`. Set it to `false` to restore the previous
  output limits and remove the retrieval tools.
- `threshold_chars` is the number of characters a result can contain before
  MiniBot offloads it. It defaults to `llm.max_tool_output_chars`. A configured
  threshold can lower that limit.
- `max_artifact_bytes` is the maximum UTF-8 content size of one artifact. It
  defaults to 8 MiB and supports values from 1 byte to 64 MiB. Oversized capture
  or a storage failure produces a tool error. The underlying command or tool
  may have already executed.

The Python SDK accepts the same settings through
`Config(context_offloading=ContextOffloadingConfig(...))`. Import both classes
from `minibot.config`.

## Retrieval tools

| Tool | Result |
| --- | --- |
| `read_artifact(artifact_id, offset, limit)` | An exact text slice. The default offset is 0. The maximum and default limit are the smaller of 4,096 and `llm.max_tool_output_chars`. A short result means the read reached the end. |
| `search_artifact(artifact_id, query)` | A JSON array of rows `[match_offset, snippet_offset, snippet]`. Search is case-sensitive and literal, with a query limit of 256 characters. It returns up to 20 matches within the same output limit. |

Offsets count Unicode characters from zero. The tools accept artifact IDs
from the current session and follow the same
[permissions](tool-approvals.md) and [run budgets](runtime-budget.md) as other
tools. A built-in `Task` subagent can retrieve artifacts when its tool
allowlist includes the corresponding tool. Team agents use their own session
scope.

## Storage and limits

- Artifacts live under `artifacts/` in the app home. Keep the same app home,
  workspace, session directory, and session ID when reopening a session. The
  model receives up to eight recent artifact references after compaction or
  reopening. Older artifacts remain readable by known ID.
- Artifacts persist on disk until removed. MiniBot does not expire them or
  enforce a total disk quota. Use a private app home outside the workspace if
  workspace tools or a container must not be able to open records directly.
  Local Bash still runs with the operating-system permissions of MiniBot.
- The stored content is the tool's text result. When offloaded, Bash
  keeps stdout followed by stderr, with UTF-8 replacement for invalid bytes.
  A nonzero exit preserves the ordinary error prefix and exit code. Large
  UTF-8 file reads retain line endings. An explicit line limit still applies.
- When [execution tracing](run-traces.md) is enabled, the trace records
  artifact creation, search, and reads. Trace records contain IDs and metadata
  such as sizes and invocation IDs. Queries and artifact text stay out of the
  trace.
