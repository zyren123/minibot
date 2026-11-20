"""Grade a task using recorded execution and independently observed effects."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from typing import Literal, get_args

from src.minibot.tools.result import ToolStatus
from src.minibot.traces import RunStatus


@dataclass(frozen=True)
class ToolStep:
    name: str
    status: ToolStatus | Literal["interrupted"] = "success"
    is_error: bool = False


TraceStep = ToolStep | Literal["run_waiting", "run_resumed"]


@dataclass(frozen=True)
class Segment:
    steps: tuple[TraceStep, ...]
    status: RunStatus = "completed"


@dataclass(frozen=True)
class ExpectedTask:
    segments: tuple[Segment, ...]
    operations: tuple[str, ...]
    files: Mapping[str, str | None]


class _UnusableTrace(ValueError):
    pass


def _require(condition: bool, location: str, problem: str) -> None:
    if not condition:
        raise _UnusableTrace(f"{location}: {problem}")


def _duration(value: object) -> bool:
    if type(value) is int:
        return value >= 0
    return type(value) is float and math.isfinite(value) and value >= 0


def _read_segment(path: Path, session_id: str) -> tuple[str | None, Segment]:
    rows = []
    previous_elapsed = 0
    for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        location = f"{path.name}:{sequence}"
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise _UnusableTrace(f"{location}: malformed JSON ({exc})") from exc
        _require(isinstance(row, dict), location, "expected a JSON object")
        version = row.get("version")
        _require(type(version) is int and version == 1, location, f"unsupported version {version!r}")
        _require(row.get("run_id") == path.stem and row.get("session_id") == session_id,
                 location, "run or session identity mismatch")
        _require(type(row.get("sequence")) is int and row["sequence"] == sequence,
                 location, f"expected sequence {sequence}, got {row.get('sequence')!r}")
        elapsed = row.get("elapsed_ms")
        _require(_duration(elapsed) and elapsed >= previous_elapsed, location, "invalid or decreasing elapsed_ms")
        previous_elapsed = elapsed
        rows.append(row)

    _require(bool(rows) and rows[0].get("event") == "run_start", path.name, "missing initial run_start")
    _require(rows[-1].get("event") == "run_end", path.name, "missing terminal run_end")
    start, end = rows[0], rows[-1]
    parent = start.get("parent_run_id")
    _require("parent_run_id" in start and (parent is None or isinstance(parent, str) and bool(parent)),
             path.name, "invalid parent_run_id")
    _require(start.get("scope") == "agent_tools", path.name, "unsupported trace scope")
    try:
        started_at = datetime.fromisoformat(start.get("started_at"))
    except (TypeError, ValueError) as exc:
        raise _UnusableTrace(f"{path.name}: missing or invalid started_at") from exc
    _require(started_at.utcoffset() == timedelta(0), path.name, "started_at must be UTC")
    _require(end.get("status") in get_args(RunStatus) and _duration(end.get("duration_ms")),
             path.name, "invalid terminal status or duration")

    steps: list[TraceStep] = []
    seen_invocations = set()
    active = None
    waiting = False
    for sequence, row in enumerate(rows[1:-1], 2):
        location = f"{path.name}:{sequence}"
        event = row.get("event")
        if event in ("tool_start", "tool_end"):
            invocation, call_id, name = (row.get(key) for key in ("invocation_id", "tool_call_id", "tool_name"))
            _require(all(isinstance(value, str) for value in (invocation, call_id, name)) and bool(invocation),
                     location, "invalid tool invocation identity")
            identity = (invocation, call_id, name)
            if event == "tool_start":
                _require(not waiting and active is None, location, "tool_start while waiting or executing a tool")
                _require(invocation not in seen_invocations, location, "reused invocation_id")
                seen_invocations.add(invocation)
                active = identity
            else:
                _require(identity == active, location, "tool_end does not match its tool_start")
                status, is_error = row.get("status"), row.get("is_error")
                _require(status in (*get_args(ToolStatus), "interrupted")
                         and type(is_error) is bool and _duration(row.get("duration_ms")),
                         location, "invalid tool status, error flag or duration")
                steps.append(ToolStep(name, status, is_error))
                active = None
        elif event in ("run_waiting", "run_resumed"):
            _require(active is None and waiting == (event == "run_resumed"),
                     location, f"unexpected {event} in the current segment state")
            waiting = event == "run_waiting"
            steps.append(event)
        elif event == "tool_approval":
            _require(
                isinstance(row.get("approval_id"), str) and bool(row["approval_id"])
                and isinstance(row.get("tool_name"), str)
                and type(row.get("approved")) is bool and row.get("source") in ("policy", "human", "auto_review"),
                location, "invalid tool approval metadata",
            )
        elif event in (
            "subagent_start", "subagent_end", "subagent_model_start", "subagent_model_end",
            "subagent_tool_start", "subagent_tool_end",
        ):
            _require(
                active is not None and active[2] == "Task" and row.get("parent_invocation_id") == active[0]
                and isinstance(row.get("child_run_id"), str) and bool(row["child_run_id"]),
                location, "subagent record has no matching parent Task invocation",
            )
        else:
            raise _UnusableTrace(f"{location}: unexpected event {event!r}")

    _require(active is None, path.name, "missing tool_end before run_end")
    _require(end["status"] != "waiting_for_user" or waiting, path.name, "missing run_waiting before pause")
    _require(end["status"] != "completed" or not waiting, path.name, "run completed while waiting for an answer")
    return parent, Segment(tuple(steps), end["status"])


def _read_trace(directory: Path, session_id: str) -> list[Segment]:
    by_parent: dict[str | None, tuple[str, Segment]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        parent, segment = _read_segment(path, session_id)
        _require(parent not in by_parent, path.name, f"multiple segments with parent_run_id {parent!r}")
        by_parent[parent] = (path.stem, segment)

    _require(bool(by_parent), str(directory), "no JSONL segments found")
    _require(None in by_parent, str(directory), "missing root segment")
    segments = []
    parent = None
    while parent in by_parent:
        if segments:
            _require(segments[-1].status == "waiting_for_user", str(directory),
                     f"resumed segment links to {parent}, which did not pause")
        parent, segment = by_parent.pop(parent)
        segments.append(segment)
    _require(not by_parent, str(directory), "segments disconnected from the root parent chain")
    return segments


def _describe_steps(steps: tuple[TraceStep, ...]) -> str:
    return ", ".join(
        step if isinstance(step, str) else f"{step.name}({step.status}, is_error={step.is_error})"
        for step in steps
    ) or "(none)"


def _read_effect(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", newline="") as file:
            return file.read()
    except FileNotFoundError:
        return None


def evaluate_task(
    workspace: Path, trace_dir: Path, session_id: str, expected: ExpectedTask,
) -> list[str]:
    failures = []
    try:
        actual = _read_trace(trace_dir, session_id)
    except (OSError, UnicodeError, _UnusableTrace) as exc:
        failures.append(f"Unusable trace: {exc}")
    else:
        if len(actual) != len(expected.segments):
            failures.append(f"Trace segment count: expected {len(expected.segments)}, got {len(actual)}")
        for index, (want, got) in enumerate(zip(expected.segments, actual), 1):
            if got.status != want.status:
                failures.append(f"Trace segment {index} terminal status: expected {want.status}, got {got.status}")
            if got.steps != want.steps:
                failures.append(
                    f"Trace segment {index} steps: expected {_describe_steps(want.steps)}; "
                    f"got {_describe_steps(got.steps)}"
                )

    try:
        operations = tuple((_read_effect(workspace / "operations.log") or "").splitlines())
        if operations != expected.operations:
            failures.append(f"Operations: expected {expected.operations!r}, got {operations!r}")
    except (OSError, UnicodeError) as exc:
        failures.append(f"Operations: cannot read journal ({exc})")

    for filename, contents in expected.files.items():
        try:
            actual_contents = _read_effect(workspace / filename)
        except (OSError, UnicodeError) as exc:
            failures.append(f"Artifact {filename}: cannot read file ({exc})")
            continue
        if actual_contents != contents:
            failures.append(f"Artifact {filename}: expected {contents!r}, got {actual_contents!r}")
    return failures
