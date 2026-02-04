"""Rich-based progress renderables (optional dependency)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem = seconds - (minutes * 60)
    return f"{minutes}m {rem:0.1f}s"


@dataclass
class SubagentStatus:
    agent_type: str
    description: str
    start_time: float
    tool_count: int = 0
    phase: str = "starting"
    current_tool: str | None = None
    extra: str | None = None
    _last_update: float = field(default_factory=time.time, init=False, repr=False)

    def tick(self) -> None:
        # Rich Live may re-render in a background thread; keep mutations tiny and atomic.
        self._last_update = time.time()

    def __rich__(self):  # type: ignore[override]
        from rich.columns import Columns
        from rich.spinner import Spinner
        from rich.text import Text

        elapsed = time.time() - self.start_time
        left = Text()
        left.append(f"[{self.agent_type}] ", style="bold cyan")
        left.append(self.description, style="white")

        mid = Text()
        mid.append(" • ", style="bright_black")
        mid.append(f"{self.tool_count} tools", style="magenta")

        right = Text()
        right.append(" • ", style="bright_black")
        right.append(_format_elapsed(elapsed), style="green")

        phase = Text()
        phase.append(" • ", style="bright_black")
        phase.append(self.phase, style="bright_black")
        if self.current_tool:
            phase.append(" ", style="bright_black")
            phase.append(self.current_tool, style="cyan")
        if self.extra:
            phase.append(" — ", style="bright_black")
            phase.append(self.extra, style="bright_black")

        return Columns(
            [
                Spinner("dots", style="cyan"),
                left,
                mid,
                right,
                phase,
            ],
            expand=True,
            equal=False,
        )

