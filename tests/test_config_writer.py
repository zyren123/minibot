import yaml
from pathlib import Path
from src.minibot.config.writer import (
    save_subagents_config,
    save_mcp_server_enabled,
    update_mcp_server_config,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_save_subagents_config_updates_file(tmp_path):
    config_path = tmp_path / "config" / "default.yaml"
    _write(config_path, "llm:\n  model: gpt-4\n")

    agents = {"explore": {"enabled": True, "skills_enabled": True}}
    save_subagents_config(config_path, agents)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["subagents"]["explore"]["skills_enabled"] is True
    assert data["llm"]["model"] == "gpt-4"


def test_save_mcp_server_enabled_toggles_server_state(tmp_path):
    config_path = tmp_path / "config" / "mcp_servers.yaml"
    _write(
        config_path,
        "enabled: true\nservers:\n  - name: duck\n    transport: stdio\n    enabled: true\n",
    )

    save_mcp_server_enabled(config_path, "duck", False)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["servers"][0]["enabled"] is False

    save_mcp_server_enabled(config_path, "duck", True)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["servers"][0]["enabled"] is True


def test_update_mcp_server_config_updates_stdio_fields(tmp_path):
    config_path = tmp_path / "config" / "mcp_servers.yaml"
    _write(
        config_path,
        (
            "enabled: true\n"
            "servers:\n"
            "  - name: duck\n"
            "    transport: stdio\n"
            "    command: old-cmd\n"
            "    args: [\"--old\"]\n"
            "    enabled: true\n"
        ),
    )

    saved = update_mcp_server_config(
        config_path,
        "duck",
        {"command": "new-cmd", "args": ["--new", "value"], "enabled": False},
    )

    assert saved["command"] == "new-cmd"
    assert saved["args"] == ["--new", "value"]
    assert saved["enabled"] is False

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["servers"][0]["command"] == "new-cmd"
    assert data["servers"][0]["args"] == ["--new", "value"]
    assert data["servers"][0]["enabled"] is False
