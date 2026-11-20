# Runtime budgets

MiniBot applies a cumulative budget to each execution segment. The main Agent and
its built-in `Task` subagents spend from the same budget. Each independent Team
worker run and each resume from a persisted question starts a new segment.

Set limits in the global `config/default.yaml` under the MiniBot app home. An
existing file without this section uses the defaults shown below. Values must be
positive. `max_wall_time` is in seconds and accepts fractional values.

```yaml
runtime_budget:
  max_llm_calls: 30
  max_tool_calls: 100
  max_wall_time: 600
  max_input_tokens: 200000
  max_output_tokens: 50000
```

For the Python SDK, pass the same policy on `Config`.

```python
from minibot import Minibot
from minibot.config import Config, RuntimeBudgetConfig

bot = Minibot(config=Config(runtime_budget=RuntimeBudgetConfig(max_llm_calls=12)))
```

A model attempt counts when MiniBot dispatches it. Streaming retries through the
non-streaming path and compaction each use a call. Retries internal to the model
client or HTTP transport count as part of that single attempt. If a final reply
is ready and the remaining budget cannot start optional compaction, MiniBot
keeps the reply. A tool attempt counts before execution, including calls refused
by policy or rejected during argument parsing.

The token limits use an estimate before dispatch and use provider-reported
prompt and completion counts when available. The requested output cap uses the
remaining output allowance. Without reported counts, output accounting estimates
visible text, reasoning, and tool call arguments, including partial output
received before a streaming fallback. Input estimates can differ
from the provider's tokenizer. A measured overrun stops the run before the next
tool or model request.

The deadline cancels a pending model request or tool execution and allows
cooperative cleanup to finish. The deadline pauses while the Agent waits for a
live human answer. Synchronous blocking code, work detached by custom tools, and
provider activity after cancellation may outlive the deadline.

A stop emits a `system` event with `data.code` set to `budget_exceeded` and
`data.limit` naming the exhausted limit. Direct `Agent.run_loop` raises
`RuntimeBudgetExceeded`. SDK chat exposes the reason in the assistant error
message. With [tracing](run-traces.md) enabled, the terminal record carries the
same status and limit. Tool results already returned in the current batch remain
in history. Skipped calls in that batch receive a refusal without running.
