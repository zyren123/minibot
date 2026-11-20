"""Run fixed file tasks through MiniBot against an explicitly selected provider."""

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, get_args
from unittest.mock import patch

from openai import APIError, AuthenticationError, NotFoundError, PermissionDeniedError

from .sdk import Minibot
from .config.schema import (
    BashConfig,
    Config,
    HooksConfig,
    LLMConfig,
    MCPConfig,
    MemoryConfig,
    RuntimeBudgetConfig,
    SessionConfig,
    TeamsConfig,
    ToolsConfig,
)
from .core.client import LLMClient
from .skills.loader import SkillLoader
from .traces import RunStatus


FILE_TOOLS = {"read_file", "write_file", "edit_file"}
BUDGET = RuntimeBudgetConfig(
    max_llm_calls=10, max_tool_calls=16, max_wall_time=60,
    max_input_tokens=50_000, max_output_tokens=6_000,
)


@dataclass(frozen=True)
class Scenario:
    prompt: str
    initial_files: dict[str, str]
    expected_files: dict[str, str]


SCENARIOS = {
    "create-note": Scenario(
        "Create notes/status.txt with these two lines in order: 'Status: ready' and "
        "'Owner: Café'. End both lines with a newline. Write the file in the workspace.",
        {},
        {"notes/status.txt": "Status: ready\nOwner: Café\n"},
    ),
    "update-inventory": Scenario(
        "Update inventory.txt so the apples count is 4. Preserve its other lines and line endings. "
        "Copy the final pears line, with a trailing newline, to pears.txt.",
        {"inventory.txt": "Fruit stock\napples: 2\npears: 7\n"},
        {"inventory.txt": "Fruit stock\napples: 4\npears: 7\n", "pears.txt": "pears: 7\n"},
    ),
}


@dataclass(frozen=True)
class Trial:
    repetition: int
    passed: bool
    status: str
    model_invocations: int
    tool_invocations: int | None
    input_tokens: int | None
    output_tokens: int | None


def total(values: list[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def usage_tokens(usage: Any, field: str) -> int | None:
    value = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
    return value if type(value) is int and value >= 0 else None


class MeasuredClient:
    def __init__(self, client: LLMClient):
        self.client = client
        self.invocations = 0
        self.input_tokens: int | None = 0
        self.output_tokens: int | None = 0
        self.error: str | None = None

    async def create_message_async(self, **kwargs):
        self.invocations += 1
        self.error = None
        try:
            response = await self.client.create_message_async(**kwargs)
        except (Exception, asyncio.CancelledError) as exc:
            self.input_tokens = self.output_tokens = None
            if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
                self.error = "provider_authentication"
            elif isinstance(exc, NotFoundError):
                self.error = "provider_model_or_endpoint_not_found"
            elif isinstance(exc, APIError):
                self.error = "provider_error"
            raise

        usage = getattr(response, "usage", None)
        self.input_tokens = total([self.input_tokens, usage_tokens(usage, "prompt_tokens")])
        self.output_tokens = total([self.output_tokens, usage_tokens(usage, "completion_tokens")])
        return response


def make_bot(directory: Path, workspace: Path, model: str, api_key: str) -> Minibot:
    config = Config(
        workdir=workspace,
        app_home=directory / "app",
        skills_dir=directory / "skills",
        trace_dir=directory / "traces",
        llm=LLMConfig(api_key=api_key, model=model, stream_enabled=False),
        tools=ToolsConfig(bash=BashConfig(backend="disabled")),
        hooks=HooksConfig(enabled=False),
        mcp=MCPConfig(enabled=False),
        memory=MemoryConfig(enabled=False),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=False),
        runtime_budget=BUDGET,
    )
    # The loader normally reads external skill descriptions before the SDK constructs its prompt.
    with patch.object(SkillLoader, "COMMON_SKILL_DIRS", ()):
        bot = Minibot(
            config=config, allow_subagent_delegation=False, allow_team_tools=False,
            system_prompt="Use only the advertised file tools. Resolve file paths relative to the workspace.",
        )
    registry = bot.agent.tool_registry
    for tool in registry.get_all():
        if tool.name not in FILE_TOOLS:
            registry.unregister(tool.name)
    return bot


def trace_result(directory: Path) -> tuple[str, int | None]:
    try:
        paths = list(directory.glob("*.jsonl"))
        if len(paths) != 1:
            return "trace_unavailable", None
        rows = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return "trace_unavailable", None
    if not rows or any(not isinstance(row, dict) for row in rows):
        return "trace_unavailable", None
    if rows[0].get("event") != "run_start" or rows[-1].get("event") != "run_end":
        return "trace_unavailable", None
    status = rows[-1].get("status")
    if status not in get_args(RunStatus):
        return "trace_unavailable", None
    return status, sum(row.get("event") == "tool_start" for row in rows)


def workspace_matches(workspace: Path, expected: dict[str, str]) -> bool:
    files = {path.relative_to(workspace).as_posix(): path for path in workspace.rglob("*") if path.is_file()}
    return files.keys() == expected.keys() and all(
        files[name].read_bytes() == content.replace("\n", os.linesep).encode("utf-8")
        for name, content in expected.items()
    )


async def run_trial(model: str, api_key: str, scenario: Scenario, repetition: int) -> Trial:
    with TemporaryDirectory(prefix="minibot-eval-") as temporary:
        directory = Path(temporary).resolve()
        workspace = directory / "workspace"
        workspace.mkdir()
        for name, content in scenario.initial_files.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        try:
            bot = make_bot(directory, workspace, model, api_key)
        except Exception:
            return Trial(repetition, False, "configuration_error", 0, None, None, None)

        client = MeasuredClient(bot.agent.client)
        bot.agent.client = client
        try:
            await bot.chat(scenario.prompt)
        except Exception:
            status, tool_calls = "runtime_error", None
        else:
            status, tool_calls = trace_result(directory / "traces")
        finally:
            try:
                await client.client.close()
            except Exception:
                status = "runtime_error"

        if status == "failed":
            status = client.error or "runtime_error"
        if status == "completed":
            try:
                status = "passed" if workspace_matches(workspace, scenario.expected_files) else "output_mismatch"
            except OSError:
                status = "workspace_unavailable"
        return Trial(
            repetition, status == "passed", status, client.invocations, tool_calls,
            client.input_tokens, client.output_tokens,
        )


def summarize(trials: list[Trial]) -> dict[str, int | float | None]:
    passed = sum(trial.passed for trial in trials)
    return {
        "trials": len(trials),
        "passed": passed,
        "pass_rate": passed / len(trials),
        "model_invocations": sum(trial.model_invocations for trial in trials),
        "tool_invocations": total([trial.tool_invocations for trial in trials]),
        "input_tokens": total([trial.input_tokens for trial in trials]),
        "output_tokens": total([trial.output_tokens for trial in trials]),
    }


async def evaluate(models: list[str], tasks: list[str], repetitions: int, api_key: str) -> dict[str, Any]:
    results = []
    for model in models:
        model_trials = []
        scenario_results = []
        for name in tasks:
            trials = [await run_trial(model, api_key, SCENARIOS[name], i) for i in range(1, repetitions + 1)]
            model_trials.extend(trials)
            scenario_results.append({
                "task": name, "summary": summarize(trials), "trials": [asdict(trial) for trial in trials],
            })
        results.append({"model": model, "summary": summarize(model_trials), "scenarios": scenario_results})
    return {"runtime_budget": asdict(BUDGET), "models": results}


def positive_int(argument: str) -> int:
    try:
        value = int(argument)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, action="append", help="Provider model ID. Repeat to compare models.")
    parser.add_argument("--task", action="append", choices=SCENARIOS, help="Task to run. Defaults to every task.")
    parser.add_argument("--repetitions", type=positive_int, default=1, help="Trials per task per model (default: 1).")
    args = parser.parse_args(argv)
    if any(not model.strip() for model in args.model):
        parser.error("--model must be nonempty")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        parser.error("set OPENAI_API_KEY to opt in to a provider run")

    report = asyncio.run(evaluate(args.model, args.task or list(SCENARIOS), args.repetitions, api_key))
    print(json.dumps(report, indent=2))
    return int(any(model["summary"]["pass_rate"] < 1 for model in report["models"]))


if __name__ == "__main__":
    raise SystemExit(main())
