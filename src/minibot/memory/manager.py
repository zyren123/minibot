"""Memory manager for persistent project-level memory."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from ..config.schema import MemoryConfig


class MemoryManager:
    """Manages long-term and daily memory files."""

    def __init__(self, config: MemoryConfig, workdir: Path):
        self.config = config
        self.memory_dir = workdir / config.memory_dir
        self.long_term_file = self.memory_dir / "LONG_TERM.md"
        self.daily_dir = self.memory_dir / "daily"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure memory directories exist."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    def _today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def _validate_date(self, date: str) -> str:
        if not self._DATE_RE.match(date):
            raise ValueError("expected YYYY-MM-DD")
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("invalid date value") from exc
        return date

    def _daily_path(self, date: str | None = None) -> Path:
        date = date or self._today_str()
        date = self._validate_date(date)
        return self.daily_dir / f"{date}.md"

    # --- Long-term memory ---

    def read_long_term(self) -> str:
        """Read long-term memory, truncated to max_lines."""
        if not self.long_term_file.exists():
            return ""
        lines = self.long_term_file.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.config.long_term_max_lines:
            lines = lines[: self.config.long_term_max_lines]
            lines.append(f"\n... (truncated at {self.config.long_term_max_lines} lines)")
        return "\n".join(lines)

    def write_long_term(self, content: str) -> str:
        """Overwrite long-term memory."""
        self.long_term_file.write_text(content, encoding="utf-8")
        return f"Long-term memory updated ({len(content)} chars)."

    def append_long_term(self, content: str) -> str:
        """Append content to long-term memory."""
        existing = (
            self.long_term_file.read_text(encoding="utf-8")
            if self.long_term_file.exists()
            else ""
        )
        separator = "\n" if existing and not existing.endswith("\n") else ""
        self.long_term_file.write_text(existing + separator + content, encoding="utf-8")
        return f"Appended to long-term memory ({len(content)} chars)."

    def _replace_text(self, existing: str, old: str, new: str) -> tuple[str, int]:
        total = existing.count(old)
        if total == 0:
            return existing, 0
        return existing.replace(old, new), total

    def replace_long_term(self, old: str, new: str) -> str:
        """Replace text in long-term memory."""
        if not self.long_term_file.exists():
            return "Long-term memory is empty; no replacements."
        existing = self.long_term_file.read_text(encoding="utf-8")
        updated, replaced = self._replace_text(existing, old, new)
        if replaced == 0:
            return "No matches in long-term memory."
        self.long_term_file.write_text(updated, encoding="utf-8")
        return f"Replaced {replaced} occurrence(s) in long-term memory."

    # --- Daily memory ---

    def read_daily(self, date: str | None = None) -> str:
        """Read daily memory for a given date (default: today)."""
        path = self._daily_path(date)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_daily(self, content: str, date: str | None = None) -> str:
        """Overwrite daily memory for a given date (default: today)."""
        path = self._daily_path(date)
        path.write_text(content, encoding="utf-8")
        date_label = date or self._today_str()
        return f"Daily memory for {date_label} updated ({len(content)} chars)."

    def append_daily(self, content: str, date: str | None = None) -> str:
        """Append content to daily memory."""
        path = self._daily_path(date)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        separator = "\n" if existing and not existing.endswith("\n") else ""
        path.write_text(existing + separator + content, encoding="utf-8")
        date_label = date or self._today_str()
        return f"Appended to daily memory for {date_label}."

    def replace_daily(
        self,
        old: str,
        new: str,
        date: str | None = None,
    ) -> str:
        """Replace text in daily memory."""
        path = self._daily_path(date)
        if not path.exists():
            date_label = date or self._today_str()
            return f"Daily memory for {date_label} is empty; no replacements."
        existing = path.read_text(encoding="utf-8")
        updated, replaced = self._replace_text(existing, old, new)
        if replaced == 0:
            return "No matches in daily memory."
        path.write_text(updated, encoding="utf-8")
        date_label = date or self._today_str()
        return f"Replaced {replaced} occurrence(s) in daily memory for {date_label}."

    # --- Context for system prompt ---

    def get_context_for_prompt(self) -> str:
        """Build memory context to inject into the system prompt."""
        sections: list[str] = []

        # Long-term memory
        long_term = self.read_long_term()
        if long_term:
            sections.append(f"### Long-term Memory\n{long_term}")

        # Today's daily memory
        today = self._today_str()
        today_content = self.read_daily(today)
        if today_content:
            sections.append(f"### Daily Memory ({today})\n{today_content}")

        # Recent daily memories (lookback)
        for i in range(1, self.config.daily_lookback_days + 1):
            past_date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            past_content = self.read_daily(past_date)
            if past_content:
                sections.append(f"### Daily Memory ({past_date})\n{past_content}")

        return "\n\n".join(sections)

    def list_daily_files(self) -> list[str]:
        """List all daily memory file dates, sorted descending."""
        files = sorted(self.daily_dir.glob("*.md"), reverse=True)
        return [f.stem for f in files]
