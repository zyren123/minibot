# Run file-task evaluations with a model

From a repository checkout, set `OPENAI_API_KEY` for an OpenAI-compatible
provider. For an alternative provider, set `OPENAI_BASE_URL` to its API base URL.
Run the evaluation with a model ID from that provider.

```sh
uv run python -m minibot.eval_model --model MODEL_ID --repetitions 3 > model-eval.json
```

The script sends synthetic file-task prompts to the provider. Each trial runs
the MiniBot SDK in its own temporary workspace with the built-in read, write,
and edit file tools. Use `--task create-note` or `--task update-inventory` to
select a task. Repeat `--model` to compare models on the same endpoint.

Read the JSON report for individual trial outcomes and per-task and per-model
pass rates. A trial passes when the run completes and the resulting files match
the expected set and text. The report counts MiniBot model requests and tool
attempts, plus provider-reported input and output tokens. A token total is
`null` if any contributing request lacks that count. Transport retries fall
within a single MiniBot request. Each trial has a 60-second active runtime
budget, with the other limits listed in the report.

Exit code `0` means all trials passed, `1` means at least one trial failed, and
`2` means argument validation failed or the API key was absent. Run the
[offline task regression checks](run-traces.md#run-the-task-regression-checks)
for deterministic tests without provider credentials.
