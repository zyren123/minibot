import builtins
from pathlib import Path

from src.minibot.config import load_config


def test_memory_config_parses_backend_database_url_and_active_namespace(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    config = load_config(workdir=project_root, app_home=app_home, project_root=project_root)

    assert config.memory.backend == "sqlite"
    assert config.memory.database_url is None
    assert config.memory.active_namespace is None
    assert config.memory.provider == "file"
    assert config.memory.novelmemo_connection == "vault"
    assert config.memory.novelmemo_mcp_url is None


def test_novelmemo_workspace_path_loads_from_utf8_settings(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir()
    settings = app_home / "config" / "default.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "memory:\n  provider: novelmemo\n  novelmemo_vault: 写作记忆\n"
        "  novelmemo_connection: mcp\n  novelmemo_mcp_url: https://memory.example.invalid/mcp\n",
        encoding="utf-8",
    )
    original_open = builtins.open

    def check_memory_settings_encoding(file, *args, **kwargs):
        if isinstance(file, Path | str) and Path(file) == settings:
            assert kwargs.get("encoding") == "utf-8"
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", check_memory_settings_encoding)
    config = load_config(workdir=project_root, app_home=app_home, project_root=project_root)

    assert config.memory.provider == "novelmemo"
    assert config.memory.novelmemo_vault == str(app_home / "写作记忆")
    assert config.memory.novelmemo_connection == "mcp"
    assert config.memory.novelmemo_mcp_url == "https://memory.example.invalid/mcp"

