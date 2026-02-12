from pathlib import Path

from src.minibot.utils.path import resolve_app_home, resolve_project_root


def test_resolve_app_home_prefers_minibot_home(monkeypatch, tmp_path):
    custom_home = tmp_path / "custom-home"
    monkeypatch.setenv("MINIBOT_HOME", str(custom_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert resolve_app_home() == custom_home.resolve()


def test_resolve_app_home_uses_xdg_when_no_minibot_home(monkeypatch, tmp_path):
    xdg_home = tmp_path / "xdg"
    monkeypatch.delenv("MINIBOT_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))

    assert resolve_app_home() == (xdg_home / "minibot").resolve()


def test_resolve_project_root_from_nested_git_dir(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    (repo / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)

    assert resolve_project_root(nested) == repo.resolve()
    assert resolve_project_root(Path(nested)) == repo.resolve()
