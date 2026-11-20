# Durable execution

Enable durable execution in `$MINIBOT_HOME/config/default.yaml` or when
constructing the SDK. The setting defaults to `false`.

```yaml
durable_execution: true
```

```python
from minibot import Minibot

bot = Minibot(workdir=".", session_id="your-session", durable_execution=True)
result = await bot.chat("Write the summary, then verify it.")
```

On restart, open the same app home, workspace configuration, and session.
Use `list_sessions()` if you need the session ID.

```python
bot = Minibot(workdir=".", session_id="your-session", durable_execution=True)
for run in bot.list_runs(incomplete_only=True):
    print(run.run_id, run.status, run.segment)
    for step in bot.list_run_steps(run.run_id):
        print(step.ordinal, step.name, step.status, step.artifact_id)
```

Call `await bot.resume_run(run_id)` for the completed response, or iterate
`bot.resume_run_stream(run_id)` for live events and approval requests. A
`system` event with `data.code = "durable_run_started"` contains the durable
run ID and segment number. Existing stream consumers can continue using the
same model, tool, and approval events.

One unfinished run can occupy a session at a time. Continue or abandon it
before starting another turn in that session. A completed run can be read
again through `resume_run`. MiniBot returns its stored result while leaving
the current session history intact.
The WebUI also waits for the unfinished run to be resolved before deleting
the session or deleting or regenerating a turn in it.

## Pause and recover

`bot.pause_run(run_id)` records a pause request for an active run. MiniBot
honors it at the next model or tool boundary, or while waiting for a question
or approval. A tool already executing can finish and record its result. Each
explicit resume starts a new runtime budget segment. Current tool permissions
apply to any invocation that still needs to execute.

At restart, read the run and step states. An `interrupted` run can usually be
resumed. A `waiting` run with a user question can present the question again
on the resumed stream. Interrupting an approval wait invalidates the old
approval ID. An invocation that still requires approval gets a fresh request
when it resumes.

`recovery_required` means the process exited after entering a step that
could have effects and before recording the outcome. Check the destination
system to establish what happened. Use
`bot.resolve_run_step(run_id, step_id, output, is_error=False)` to record the
verified result. Then resume the run. If you cannot establish a safe outcome,
`bot.abandon_run(run_id)` closes it. Abandoning a run leaves any effects that
already occurred in place. The SDK scopes every operation to the selected
workspace and session.

MiniBot can retry an interrupted invocation of an exact built-in
`read_file`, `read_artifact`, `search_artifact`, or `askuserquestion` tool when
the tool had no pre- or post-tool hooks and remains eligible when it resumes.
Bash, file writes and edits, custom or MCP tools, and the parent `Task` tool
require operator recovery when the outcome is lost. Calls that already
committed their results are reused in order, including offloaded artifact
references.

## CLI and WebUI

On the CLI, select the original session with `/session load`. Run
`/run list` to find unfinished runs, then use `/run resume ID` or
`/run abandon ID`. Use the SDK to record the verified outcome of an
ambiguous tool step.

With durable execution enabled on the server, the existing WebUI Ask User
interaction resumes its durable run and continues the tool batch once the
user answers. General run discovery, pause, and operator recovery are SDK
operations. File checkpoints continue to handle workspace edits independently.

## What is persisted

MiniBot stores a versioned runtime checkpoint and an ordered tool journal in
`runs/journal.sqlite3` under the app home. The checkpoint contains messages
and the current model tool batch. Each tool step has its own ID even if the
provider repeats a tool call ID. Model calls can be repeated from the last
committed checkpoint if the process exits before recording the response.
MiniBot holds an OS lock for the active run and its session, and checks the
canonical workspace, execution profile, and session store before attachment.
Session history remains JSONL. A trace, when enabled, is recorded separately
for each execution segment.

The journal stores prompts, tool arguments, and result previews. Protect the
app home as you would your session history. Keep the app home stable across
processes sharing a session. This version recovers the single-agent loop.
Team and subagent process state remains outside the journal. Recovery of
external effects depends on what the destination system can verify.
