"""Allowlisted, per-run execution records, separate from rich stream payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Literal, TextIO
import uuid

from .events import StreamEvent

RunStatus = Literal["completed", "failed", "interrupted", "waiting_for_user"]


@dataclass(frozen=True)
class Invocation:
    id: str
    call_id: str
    name: str
    started: float


class RunTrace:
    """One producer owns one file. Write failures never replace runtime outcomes."""

    def __init__(
        self, directory: Path, session_id: str, *, run_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self.run_id = uuid.UUID(run_id).hex if run_id else uuid.uuid4().hex
        self.session_id = session_id
        self.started = time.monotonic()
        self.sequence = 0
        self.pending: Invocation | None = None
        self.waiting = False
        self._file: TextIO | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self._file = (directory / f"{self.run_id}.jsonl").open("x", encoding="utf-8")
        except (OSError, ValueError):
            self._disable()
        self._write(
            "run_start", scope="agent_tools", parent_run_id=parent_run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def _disable(self) -> None:
        logging.getLogger(__name__).warning("Execution trace unavailable; recording disabled for this run.")
        file, self._file = self._file, None
        if file is not None:
            try:
                file.close()
            except (OSError, ValueError):
                pass

    def _write(self, event: str, **fields: Any) -> None:
        if self._file is None:
            return
        self.sequence += 1
        record = {
            "version": 1, "event": event, "run_id": self.run_id,
            "session_id": self.session_id, "sequence": self.sequence,
            "elapsed_ms": (time.monotonic() - self.started) * 1000,
            **fields,
        }
        try:
            self._file.write(json.dumps(record, ensure_ascii=True) + "\n")
            self._file.flush()
        except (OSError, ValueError):
            self._disable()

    def finish_tool(self, *, status: str, is_error: bool, **fields: Any) -> None:
        invocation, self.pending = self.pending, None
        if invocation is None:
            return
        self._write(
            "tool_end", invocation_id=invocation.id, tool_call_id=invocation.call_id,
            tool_name=invocation.name, status=status, is_error=is_error,
            duration_ms=(time.monotonic() - invocation.started) * 1000, **fields,
        )

    def observe(self, event: StreamEvent) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            invocation = Invocation(
                uuid.uuid4().hex, event.get("tool_call_id", ""),
                event.get("tool_name", ""), time.monotonic(),
            )
            self.pending = invocation
            self._write(
                "tool_start", invocation_id=invocation.id,
                tool_call_id=invocation.call_id, tool_name=invocation.name,
            )
        elif kind == "tool_result":
            fields: dict[str, Any] = {}
            if "tool_exit_code" in event:
                fields["exit_code"] = event["tool_exit_code"]
            if "tool_hook_phase" in event:
                fields["hook_phase"] = event["tool_hook_phase"]
            self.finish_tool(
                status=event.get("tool_status", "error"),
                is_error=event.get("is_error", True), **fields,
            )
        elif kind == "ask_user_question":
            self.finish_tool(status="success", is_error=False)
            self.waiting = True
            self._write("run_waiting")
        elif kind == "ask_user_answer_received":
            self.waiting = False
            self._write("run_resumed")

    def close(self, status: RunStatus) -> None:
        self.finish_tool(status="interrupted" if status == "interrupted" else "error", is_error=True)
        self._write("run_end", status=status, duration_ms=(time.monotonic() - self.started) * 1000)
        file, self._file = self._file, None
        if file is not None:
            try:
                file.close()
            except (OSError, ValueError):
                self._disable()
