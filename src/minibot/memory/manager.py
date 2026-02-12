"""Memory manager for persistent project-level memory."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..config.schema import MemoryConfig


class MemoryManager:
    """Manages long-term and daily memory files."""

    def __init__(self, config: MemoryConfig, app_home: Path, project_root: Path):
        self.config = config
        self.app_home = app_home.resolve()
        self.project_root = project_root.resolve()
        self.memory_dir = self._resolve_memory_dir(config.memory_dir)
        self.long_term_file = self.memory_dir / "LONG_TERM.md"
        self.daily_dir = self.memory_dir / "daily"
        self.state_dir = self.app_home / "state"
        self._migration_notice: str | None = None
        self._ensure_dirs()
        self._migrate_legacy_project_memory_once()

    def _resolve_memory_dir(self, configured_path: str) -> Path:
        path = Path(configured_path).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self.app_home / path).resolve()

    def _ensure_dirs(self) -> None:
        """Ensure memory directories exist."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _migration_state_path(self) -> Path:
        return self.state_dir / "migration_v1.json"

    def _read_migration_state(self) -> dict:
        path = self._migration_state_path()
        if not path.exists():
            return {"migrated_projects": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"migrated_projects": []}
            migrated = data.get("migrated_projects")
            if not isinstance(migrated, list):
                return {"migrated_projects": []}
            return {"migrated_projects": [str(item) for item in migrated]}
        except Exception:
            return {"migrated_projects": []}

    def _write_migration_state(self, state: dict) -> None:
        self._migration_state_path().write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _legacy_memory_dir(self) -> Path:
        return self.project_root / ".minibot" / "memory"

    def _project_migration_key(self) -> str:
        return str(self.project_root)

    def _split_blocks(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]

    def _merge_block_text(self, existing: str, incoming: str) -> str:
        blocks: list[str] = []
        seen: set[str] = set()
        for text in (existing, incoming):
            for block in self._split_blocks(text):
                if block in seen:
                    continue
                seen.add(block)
                blocks.append(block)
        if not blocks:
            return ""
        return "\n\n".join(blocks) + "\n"

    def _merge_file(self, source: Path, target: Path) -> None:
        incoming = source.read_text(encoding="utf-8")
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        merged = self._merge_block_text(existing, incoming)
        if merged:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(merged, encoding="utf-8")

    def _migrate_legacy_project_memory_once(self) -> None:
        legacy_dir = self._legacy_memory_dir()
        if not legacy_dir.exists():
            return
        if legacy_dir.resolve() == self.memory_dir:
            return

        state = self._read_migration_state()
        key = self._project_migration_key()
        migrated_projects = set(state.get("migrated_projects", []))
        if key in migrated_projects:
            return

        try:
            legacy_long_term = legacy_dir / "LONG_TERM.md"
            if legacy_long_term.exists():
                self._merge_file(legacy_long_term, self.long_term_file)

            legacy_daily_dir = legacy_dir / "daily"
            if legacy_daily_dir.exists():
                for daily_file in sorted(legacy_daily_dir.glob("*.md")):
                    self._merge_file(daily_file, self.daily_dir / daily_file.name)

            migrated_projects.add(key)
            self._write_migration_state({"migrated_projects": sorted(migrated_projects)})
        except Exception as exc:
            self._migration_notice = (
                f"legacy memory migration failed for {legacy_dir}: {exc}"
            )

    def migration_notice(self) -> str | None:
        return self._migration_notice

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
