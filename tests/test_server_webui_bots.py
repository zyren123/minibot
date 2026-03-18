import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "minibot_home"
    workdir = tmp_path / "workdir"
    home.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINIBOT_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    return {"home": home, "workdir": workdir}


@pytest.fixture()
def client(server_env: dict[str, Path]) -> TestClient:
    from src.minibot.server.app import create_app

    app = create_app(workdir=server_env["workdir"])
    return TestClient(app)


def test_bots_crud_and_config_persistence(client: TestClient, server_env: dict[str, Path]) -> None:
    resp = client.get("/api/bots")
    assert resp.status_code == 200
    bots = resp.json()
    assert any(b["bot_id"] == "default" for b in bots)

    created = client.post("/api/bots", json={"name": "Test Bot"}).json()
    bot_id = created["bot_id"]
    assert bot_id and bot_id != "default"

    cfg = client.get(f"/api/bots/{bot_id}/config").json()
    assert cfg["bot_id"] == bot_id
    assert cfg["name"] in {"Test Bot", bot_id}

    tool_plugin_path = str((server_env["workdir"] / "plugins.py").resolve())
    update = {
        "name": "Renamed Bot",
        "base_url": "http://example.test",
        "model": "gpt-test",
        "stream_enabled": True,
        "tool_plugins": [tool_plugin_path],
        "skills_disabled": ["some-skill"],
        "mcp_overrides": {"example": True},
        "soul": "You are a helpful bot.",
    }
    put = client.put(f"/api/bots/{bot_id}/config", json=update)
    assert put.status_code == 200, put.text

    cfg2 = client.get(f"/api/bots/{bot_id}/config").json()
    assert cfg2["name"] == "Renamed Bot"
    assert cfg2["base_url"] == "http://example.test"
    assert cfg2["model"] == "gpt-test"
    assert cfg2["stream_enabled"] is True
    assert cfg2["tool_plugins"] and cfg2["tool_plugins"][0].endswith("plugins.py")
    assert cfg2["skills_disabled"] == ["some-skill"]
    assert cfg2["mcp_overrides"] == {"example": True}
    assert "helpful bot" in cfg2["soul"]

    bot_home = server_env["home"] / "bots" / bot_id
    assert (bot_home / "bot.json").exists()
    assert (bot_home / "soul.md").exists()

    deleted = client.delete(f"/api/bots/{bot_id}").json()
    assert deleted["deleted"] is True
    assert not bot_home.exists()


def test_skills_endpoint_uses_configured_skills_dirs(client: TestClient, server_env: dict[str, Path]) -> None:
    skills_root = server_env["workdir"] / "skills"
    skill_dir = skills_root / "test-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test skill\n---\n\nDo the test.\n",
        encoding="utf-8",
    )

    put = client.put("/api/config", json={"skills_dirs": [str(skills_root)]})
    assert put.status_code == 200, put.text

    skills = client.get("/api/skills").json()
    names = {s["name"] for s in skills}
    assert "test-skill" in names


def test_sessions_are_isolated_and_deletable(client: TestClient) -> None:
    bot_id = client.post("/api/bots", json={"name": "Sessions Bot"}).json()["bot_id"]

    default_sid = client.post("/api/sessions", json={}).json()["session_id"]
    bot_sid = client.post(f"/api/bots/{bot_id}/sessions", json={}).json()["session_id"]

    default_sessions = client.get("/api/sessions").json()
    bot_sessions = client.get(f"/api/bots/{bot_id}/sessions").json()

    assert any(s["session_id"] == default_sid for s in default_sessions)
    assert all(s["session_id"] != bot_sid for s in default_sessions)

    assert any(s["session_id"] == bot_sid for s in bot_sessions)
    assert all(s["session_id"] != default_sid for s in bot_sessions)

    deleted = client.delete(f"/api/bots/{bot_id}/sessions/{bot_sid}").json()
    assert deleted["deleted"] is True

    missing = client.get(f"/api/bots/{bot_id}/sessions/{bot_sid}")
    assert missing.status_code == 404


def test_manager_delete_session_clears_cache(server_env: dict[str, Path]) -> None:
    from src.minibot.server.manager import AgentManager

    manager = AgentManager(workdir=server_env["workdir"])
    bot_id = manager._bot_store.create_bot(name="Cache Bot").bot_id
    sid = manager.sessions_for(bot_id).create()

    bot = asyncio.run(manager.get_bot(bot_id, sid))
    assert manager._bots.get((bot_id, sid)) is bot

    ok = asyncio.run(manager.delete_session(bot_id, sid))
    assert ok is True
    assert (bot_id, sid) not in manager._bots
