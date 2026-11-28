"""Versioned, read-only trace projection shared by future agent adapters."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import re
from typing import Literal, Protocol, TextIO

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, ValidationError

from .session.messages import normalize_tool_calls


TraceKind = Literal["message", "model", "tool", "agent", "wait", "event"]


class TraceSource(BaseModel):
    kind: str
    id: str


class TraceField(BaseModel):
    label: str
    source: str
    value: JsonValue


class TraceEvent(BaseModel):
    id: str
    parent_id: str | None = None
    kind: TraceKind
    label: str
    start_ms: float | None = None
    duration_ms: float | None = None
    status: str | None = None
    is_error: bool = False
    fields: list[TraceField] = Field(default_factory=list)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)


class TraceRun(BaseModel):
    id: str
    parent_run_id: str | None
    user_message_id: str | None
    title: str | None
    started_at: str
    duration_ms: float | None
    status: str
    events: list[TraceEvent]


class TraceSession(BaseModel):
    schema_version: Literal[1] = 1
    source: TraceSource
    session_id: str
    recording: bool
    runs: list[TraceRun]


class TraceAdapter(Protocol):
    def read_session(self, session_id: str) -> TraceSession: ...


class _SessionHistory(Protocol):
    def load(self, session_id: str) -> list[dict]: ...


class _Row(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    version: Literal[1]
    event: str
    session_id: str
    run_id: str
    sequence: int
    elapsed_ms: FiniteFloat
    bot_id: str | None = None
    parent_run_id: str | None = None
    user_message_id: str | None = None
    started_at: str | None = None
    duration_ms: FiniteFloat | None = None
    status: str | None = None
    is_error: bool = False
    message_id: str | None = None
    parent_user_message_id: str | None = None
    invocation_id: str | None = None
    parent_invocation_id: str | None = None
    child_run_id: str | None = None
    parent_step_id: str | None = None
    agent_type: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    budget_limit: str | None = None
    exit_code: int | None = None
    hook_phase: str | None = None
    usage: dict[str, int] | None = None
    approval_id: str | None = None
    approved: bool | None = None
    source: str | None = None
    artifact_id: str | None = None
    artifact_run_id: str | None = None
    artifact_tool_name: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None


_RUN_NAME = re.compile(r"[0-9a-f]{32}\.jsonl")


def _read_rows(stream: TextIO, start: _Row) -> Iterator[_Row]:
    previous = start
    for line in stream:
        if previous.event == "run_end":
            return
        try:
            row = _Row.model_validate_json(line)
        except ValidationError:
            return
        if (
            row.run_id != start.run_id or row.session_id != start.session_id
            or row.sequence != previous.sequence + 1 or row.elapsed_ms < previous.elapsed_ms
            or (row.duration_ms is not None and row.duration_ms < 0)
        ):
            return
        yield row
        previous = row


def _read_run(path: Path, session_id: str, bot_id: str, *, allow_unowned: bool) -> list[_Row]:
    if not _RUN_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8") as stream:
            start = _Row.model_validate_json(stream.readline())
            if (
                start.event != "run_start" or start.run_id != path.stem or start.session_id != session_id
                or start.sequence != 1 or start.elapsed_ms < 0 or not start.started_at
                or (start.bot_id != bot_id and not (start.bot_id is None and allow_unowned))
            ):
                return []
            if datetime.fromisoformat(start.started_at).tzinfo is None:
                return []
            return [start, *_read_rows(stream, start)]
    except (OSError, ValueError):
        return []


class _Messages:
    def __init__(self, messages: Sequence[dict]) -> None:
        self.by_id: dict[str, dict | None] = {}
        for message in messages:
            message_id = message.get("message_id")
            if isinstance(message_id, str) and message_id:
                self.by_id[message_id] = None if message_id in self.by_id else message

    def get(self, message_id: str | None, role: str) -> dict:
        message = self.by_id.get(message_id) if message_id else None
        return message if message and message.get("role") == role else {}


def _is_json(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json(item) for key, item in value.items())
    return False


def _fields(*items: tuple[str, object]) -> list[TraceField]:
    return [
        TraceField(label=label, source="session", value=value)
        for label, value in items if value is not None and _is_json(value)
    ]


def _unique_tool_arguments(message: dict, call_id: str | None, tool_name: str | None) -> str | None:
    if not call_id:
        return None
    calls = [
        call["function"]["arguments"]
        for call in normalize_tool_calls(message.get("tool_calls"))
        if call["id"] == call_id and call["function"]["name"] == tool_name
    ]
    return calls[0] if len(calls) == 1 else None


def _model_label(message: dict) -> str:
    tools = {call["function"]["name"].lower() for call in normalize_tool_calls(message.get("tool_calls"))}
    if message.get("interaction_type") == "askuserquestion" or "askuserquestion" in tools:
        return "Ask user"
    if tools.intersection({"task", "taskbatch"}):
        return "Delegate tasks"
    if tools:
        return "Call tools"
    if message.get("content"):
        return "Generate reply"
    return "Model"


@dataclass(frozen=True)
class _Span:
    kind: TraceKind
    end_event: str
    identity: Literal["child_run_id", "invocation_id"] = "invocation_id"


_SPANS: dict[str, _Span] = {
    "model_start": _Span("model", "model_end"),
    "tool_start": _Span("tool", "tool_end"),
    "subagent_start": _Span("agent", "subagent_end", "child_run_id"),
    "subagent_model_start": _Span("model", "subagent_model_end"),
    "subagent_tool_start": _Span("tool", "subagent_tool_end"),
}

_ENDS = {span.end_event: span.identity for span in _SPANS.values()}

_EVENT_LABELS = {
    "tool_approval": "Tool approval",
    "artifact_created": "Artifact created",
    "artifact_search": "Artifact search",
    "artifact_read": "Artifact read",
}

_FAILURES = {"failed", "interrupted", "error", "context_window_exceeded", "budget_exceeded"}


def _metadata(row: _Row) -> dict[str, JsonValue]:
    return row.model_dump(exclude={
        "bot_id", "session_id", "version", "elapsed_ms", "started_at", "duration_ms", "is_error", "status",
    }, exclude_none=True)


def _parent(row: _Row) -> str | None:
    if row.event in {"subagent_start", "durable_child"}:
        return row.parent_invocation_id
    return row.child_run_id


class _RunProjection:
    def __init__(self, rows: list[_Row], messages: _Messages) -> None:
        self.start = rows[0]
        self.end = rows[-1] if rows[-1].event == "run_end" else None
        self.rows = rows
        self.messages = messages
        self.spans: dict[str, tuple[_Row, _Row | None]] = {}
        endings: dict[tuple[str, str, str | None], _Row | None] = {}
        for row in rows:
            identity = _ENDS.get(row.event)
            event_id = getattr(row, identity) if identity else None
            if event_id:
                key = (row.event, event_id, row.child_run_id)
                endings[key] = None if key in endings else row
        for row in rows:
            span = _SPANS.get(row.event)
            if span is None:
                continue
            event_id = getattr(row, span.identity)
            if not event_id or event_id in self.spans:
                continue
            end = endings.get((span.end_event, event_id, row.child_run_id))
            if end is not None and (
                end.sequence <= row.sequence
                or end.tool_call_id != row.tool_call_id
                or end.tool_name != row.tool_name
            ):
                end = None
            self.spans[event_id] = (row, end)

    def _tool_fields(self, row: _Row, end: _Row | None) -> list[TraceField]:
        if row.event != "tool_start":
            return []
        result = self.messages.get(end.message_id if end else None, "tool")
        if result and result.get("tool_call_id") == row.tool_call_id and result.get("tool_name") == row.tool_name:
            return _fields(("Input", result.get("tool_args")), ("Output", result.get("content")))
        args = _unique_tool_arguments(self.messages.get(row.message_id, "assistant"), row.tool_call_id, row.tool_name)
        return _fields(("Input", args))

    def _agent_fields(self, row: _Row) -> list[TraceField]:
        if row.parent_invocation_id is None:
            return []
        pair = self.spans.get(row.parent_invocation_id)
        if pair is None or pair[0].tool_name != "Task":
            return []
        values = {field.label: field.value for field in self._tool_fields(*pair)}
        args = values.get("Input")
        return _fields(("Prompt", args.get("prompt") if isinstance(args, dict) else None), ("Return", values.get("Output")))

    def _span_events(self) -> dict[int, TraceEvent]:
        events = {}
        for event_id, (row, end) in self.spans.items():
            kind = _SPANS[row.event].kind
            if kind == "agent":
                label, body = row.agent_type or "Agent", self._agent_fields(row)
            elif kind == "tool":
                label, body = row.tool_name or "Tool", self._tool_fields(row, end)
            else:
                message = {}
                if row.event == "model_start" and end is not None and end.status == "completed":
                    message = self.messages.get(row.message_id, "assistant")
                label = _model_label(message)
                body = _fields(("Message", message.get("content") or None), ("Reasoning", message.get("reasoning")))
            evidence = _metadata(row)
            if end is not None:
                evidence["end"] = _metadata(end)
            events[row.sequence] = TraceEvent(
                id=event_id, parent_id=_parent(row), kind=kind, label=label, start_ms=row.elapsed_ms,
                duration_ms=end.duration_ms if end else None, status=end.status if end else None,
                is_error=end is not None and (end.is_error or end.status in _FAILURES),
                fields=body, evidence=evidence,
            )
        return events

    def _wait_event(self, start: _Row, end: _Row | None) -> TraceEvent:
        prompt = self.messages.get(start.message_id, "assistant").get("question_prompt")
        answer = self.messages.get(end.message_id if end else None, "user").get("content")
        evidence = _metadata(start)
        if end is not None:
            evidence["end"] = _metadata(end)
        return TraceEvent(
            id=f"wait:{start.sequence}", kind="wait", label="Wait for user", start_ms=start.elapsed_ms,
            duration_ms=end.elapsed_ms - start.elapsed_ms if end else None,
            status="completed" if end else "waiting_for_user",
            fields=_fields(("Question", prompt), ("Answer", answer)), evidence=evidence,
        )

    def _events(self) -> list[TraceEvent]:
        events = self._span_events()
        wait = None
        for row in self.rows:
            if row.event == "run_waiting":
                if wait is not None:
                    events[wait.sequence] = self._wait_event(wait, None)
                wait = row
            elif row.event == "run_resumed" and wait is not None:
                events[wait.sequence] = self._wait_event(wait, row)
                wait = None
            elif row.event == "assistant_message" and row.message_id:
                message = self.messages.get(row.message_id, "assistant")
                events[row.sequence] = TraceEvent(
                    id=f"assistant:{row.sequence}", kind="message", label="Assistant", start_ms=row.elapsed_ms,
                    fields=_fields(("Message", message.get("content")), ("Reasoning", message.get("reasoning"))),
                    evidence=_metadata(row),
                )
            elif row.event == "durable_child" and row.child_run_id:
                events[row.sequence] = TraceEvent(
                    id=f"child:{row.sequence}", kind="agent", label=row.agent_type or "Agent",
                    start_ms=row.elapsed_ms, parent_id=_parent(row), evidence=_metadata(row),
                )
            elif row.event in _EVENT_LABELS:
                events[row.sequence] = TraceEvent(
                    id=f"event:{row.sequence}", kind="event", label=_EVENT_LABELS[row.event],
                    start_ms=row.elapsed_ms, parent_id=row.invocation_id, evidence=_metadata(row),
                )
        if wait is not None:
            events[wait.sequence] = self._wait_event(wait, None)
        result = [event for _, event in sorted(events.items())]
        previous_ids: set[str] = set()
        for event in result:
            if event.parent_id not in previous_ids:
                event.parent_id = None
            previous_ids.add(event.id)
        return result

    def project(self) -> TraceRun:
        prompt = self.messages.get(self.start.user_message_id, "user").get("content")
        title = prompt if isinstance(prompt, str) and prompt.strip() else None
        events = self._events()
        if self.start.user_message_id:
            events.insert(0, TraceEvent(
                id="user:" + self.start.user_message_id, kind="message", label="User",
                fields=_fields(("Message", prompt)), evidence={"message_id": self.start.user_message_id},
            ))
        return TraceRun(
            id=self.start.run_id, parent_run_id=self.start.parent_run_id, title=title,
            user_message_id=self.start.user_message_id, started_at=self.start.started_at or "",
            duration_ms=self.end.duration_ms if self.end else None,
            status=(self.end.status if self.end else None) or "incomplete", events=events,
        )


class MiniBotTraceAdapter:
    def __init__(
        self, directory: Path | None, bot_id: str, history: _SessionHistory, *, allow_unowned: bool = False,
    ) -> None:
        self.directory = directory
        self.bot_id = bot_id
        self.history = history
        self.allow_unowned = allow_unowned

    def read_session(self, session_id: str) -> TraceSession:
        runs: list[TraceRun] = []
        if self.directory is not None:
            try:
                history = self.history.load(session_id)
            except (OSError, ValueError, TypeError):
                history = []
            messages = _Messages(history)
            unowned = _Messages([])
            for path in self.directory.glob("*.jsonl"):
                rows = _read_run(path, session_id, self.bot_id, allow_unowned=self.allow_unowned)
                if rows:
                    runs.append(_RunProjection(rows, messages if rows[0].bot_id else unowned).project())
        runs.sort(key=lambda run: (run.started_at, run.id))
        return TraceSession(
            source=TraceSource(kind="minibot", id=self.bot_id),
            session_id=session_id, recording=self.directory is not None, runs=runs,
        )
