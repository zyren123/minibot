from pathlib import Path

from src.minibot.config.schema import MemoryConfig
from src.minibot.memory.manager import MemoryManager


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_memory_manager_uses_app_home_by_default(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    manager = MemoryManager(MemoryConfig(memory_dir="memory"), app_home, project_root)

    assert manager.memory_dir == (app_home / "memory").resolve()
    assert manager.long_term_file == (app_home / "memory" / "LONG_TERM.md").resolve()
    assert manager.daily_dir == (app_home / "memory" / "daily").resolve()


def test_memory_manager_migrates_legacy_project_memory_once(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    legacy_dir = project_root / ".minibot" / "memory"

    _write(legacy_dir / "LONG_TERM.md", "A\n\nB\n")
    _write(legacy_dir / "daily" / "2026-02-10.md", "daily-a\n\ndaily-b\n")

    global_memory_dir = app_home / "memory"
    _write(global_memory_dir / "LONG_TERM.md", "A\n\nC\n")
    _write(global_memory_dir / "daily" / "2026-02-10.md", "daily-a\n\nexisting\n")

    manager = MemoryManager(MemoryConfig(memory_dir="memory"), app_home, project_root)
    long_term = manager.read_long_term()
    daily = manager.read_daily("2026-02-10")

    assert "A" in long_term
    assert "B" in long_term
    assert "C" in long_term
    assert long_term.count("A") == 1
    assert "daily-a" in daily
    assert "daily-b" in daily
    assert "existing" in daily
    assert daily.count("daily-a") == 1

    manager2 = MemoryManager(MemoryConfig(memory_dir="memory"), app_home, project_root)
    long_term2 = manager2.read_long_term()
    assert long_term2 == long_term

    marker = app_home / "state" / "migration_v1.json"
    assert marker.exists()
    assert str(project_root.resolve()) in marker.read_text(encoding="utf-8")
