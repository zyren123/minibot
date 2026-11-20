# Request context limits

MiniBot checks each model request before dispatch. It estimates the input from
the system prompt, messages, and tool definitions. The same check applies to
streaming requests, non-streaming requests, and built-in `Task` subagents.

## Configure the limits

The defaults are shown below. Set these keys under `llm` in the global
`config/default.yaml` under the MiniBot app home. SDK users can pass
`LLMConfig(max_context_tokens=..., max_tool_output_chars=..., max_tokens=...)` in
`Config(llm=...)`.

```yaml
llm:
  max_context_tokens: 100000
  max_tool_output_chars: 16000
  max_tokens: 8000
```

Set `max_context_tokens` to the model's total input and output window.
`max_tokens` sets the preferred completion cap for the main Agent and built-in
`Task` subagents. The token estimate is approximate. A provider can count the
same request differently.

## How MiniBot fits a request

MiniBot clips text tool results to `max_tool_output_chars` in the messages sent
to the model. The limit includes the truncation marker and must be at least 32
characters. Saved history and tool-result events retain the original output.

MiniBot reserves space up to the configured completion cap and remaining
[run output budget](runtime-budget.md). If the request needs more space, it
omits older turns from the model request. It retains the newest user turn,
including ask-user replies and tool calls with their results, and keeps
explicit system and developer messages. Older summaries can also be omitted.
The saved conversation keeps the omitted messages.

If the retained input cannot leave the preferred output room, MiniBot reduces
the completion cap. If the input leaves no room for a completion, MiniBot
reports `context_window_exceeded` before calling the provider. An optional
compaction request only runs when it can fit the history it needs to summarize.
