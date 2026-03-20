import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.minibot.session.manager import SessionManager


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
    default_bot = next((b for b in bots if b["bot_id"] == "default"), None)
    assert default_bot is not None
    assert default_bot["name"] == "Minibot"

    default_cfg = client.get("/api/bots/default/config").json()
    assert default_cfg["name"] == "Minibot"

    created = client.post("/api/bots", json={"name": "Test Bot"}).json()
    bot_id = created["bot_id"]
    assert bot_id and bot_id != "default"

    cfg = client.get(f"/api/bots/{bot_id}/config").json()
    assert cfg["bot_id"] == bot_id
    assert cfg["name"] in {"Test Bot", bot_id}
    assert cfg["max_context_tokens"] > 0
    assert cfg["auto_compact_threshold_tokens"] == int(cfg["max_context_tokens"] * 0.8)

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


def test_default_bot_legacy_name_is_normalized_to_minibot(server_env: dict[str, Path]) -> None:
    from src.minibot.server.app import create_app

    (server_env["home"] / "bot.json").write_text('{"name": "Default"}\n', encoding="utf-8")
    client = TestClient(create_app(workdir=server_env["workdir"]))

    bots = client.get("/api/bots").json()
    default_bot = next((b for b in bots if b["bot_id"] == "default"), None)
    assert default_bot is not None
    assert default_bot["name"] == "Minibot"

    cfg = client.get("/api/bots/default/config").json()
    assert cfg["name"] == "Minibot"


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


def test_session_load_returns_structured_message_metadata(client: TestClient, server_env: dict[str, Path]) -> None:
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    manager = SessionManager(server_env["home"] / "sessions")
    manager.overwrite(
        session_id,
        [
            {"role": "user", "content": "hello", "message_id": "msg-user-1"},
            {
                "role": "assistant",
                "content": "world",
                "message_id": "msg-assistant-1",
                "parent_user_message_id": "msg-user-1",
                "reasoning": "step by step",
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                "context_usage": {"total_tokens": 6},
            },
        ],
    )

    resp = client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["messages"][1]["message_id"] == "msg-assistant-1"
    assert body["messages"][1]["parent_user_message_id"] == "msg-user-1"
    assert body["messages"][1]["reasoning"] == "step by step"
    assert body["messages"][1]["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert body["messages"][1]["context_usage"] == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 6}


def test_message_turn_delete_removes_full_turn(client: TestClient, server_env: dict[str, Path]) -> None:
    bot_id = client.post("/api/bots", json={"name": "Delete Turn Bot"}).json()["bot_id"]
    session_id = client.post(f"/api/bots/{bot_id}/sessions", json={}).json()["session_id"]
    manager = SessionManager(server_env["home"] / "bots" / bot_id / "sessions")
    manager.overwrite(
        session_id,
        [
            {"role": "user", "content": "first", "message_id": "msg-user-1"},
            {
                "role": "assistant",
                "content": "tool preface",
                "message_id": "msg-assistant-1",
                "parent_user_message_id": "msg-user-1",
            },
            {
                "role": "tool",
                "content": "tool output",
                "message_id": "msg-tool-1",
                "parent_user_message_id": "msg-user-1",
            },
            {
                "role": "assistant",
                "content": "first answer",
                "message_id": "msg-assistant-2",
                "parent_user_message_id": "msg-user-1",
            },
            {"role": "user", "content": "second", "message_id": "msg-user-2"},
            {
                "role": "assistant",
                "content": "second answer",
                "message_id": "msg-assistant-3",
                "parent_user_message_id": "msg-user-2",
            },
        ],
    )

    resp = client.delete(f"/api/bots/{bot_id}/sessions/{session_id}/messages/msg-assistant-2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_message_ids"] == ["msg-user-1", "msg-assistant-1", "msg-tool-1", "msg-assistant-2"]
    remaining_ids = [item["message_id"] for item in body["messages"]]
    assert remaining_ids == ["msg-user-2", "msg-assistant-3"]


def test_message_regenerate_rewrites_latest_turn(
    client: TestClient,
    server_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_id = client.post("/api/bots", json={"name": "Regenerate Bot"}).json()["bot_id"]
    session_id = client.post(f"/api/bots/{bot_id}/sessions", json={}).json()["session_id"]
    session_mgr = SessionManager(server_env["home"] / "bots" / bot_id / "sessions")
    session_mgr.overwrite(
        session_id,
        [
            {"role": "user", "content": "persisted", "message_id": "msg-user-1"},
            {
                "role": "assistant",
                "content": "old answer",
                "message_id": "msg-assistant-1",
                "parent_user_message_id": "msg-user-1",
            },
        ],
    )

    async def fake_get_bot(self, requested_bot_id: str, requested_session_id: str):
        assert requested_bot_id == bot_id
        assert requested_session_id == session_id

        class FakeBot:
            async def chat(self, prompt: str, *, session_id: str | None = None):
                assert prompt == "persisted"
                assert session_id == requested_session_id
                messages = [
                    {
                        "role": "user",
                        "content": "persisted",
                        "message_id": "msg-user-new",
                    },
                    {
                        "role": "assistant",
                        "content": "new answer",
                        "message_id": "msg-assistant-new",
                        "parent_user_message_id": "msg-user-new",
                        "reasoning": "updated reasoning",
                        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
                    },
                ]
                session_mgr.overwrite(requested_session_id, messages)
                return SimpleNamespace(
                    session_id=requested_session_id,
                    messages=messages,
                    assistant_text="new answer",
                    usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
                )

            def cancel(self) -> None:
                return None

        return FakeBot()

    monkeypatch.setattr("src.minibot.server.manager.AgentManager.get_bot", fake_get_bot)

    resp = client.post(f"/api/bots/{bot_id}/sessions/{session_id}/messages/msg-assistant-1/regenerate", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["regenerated_from_message_id"] == "msg-assistant-1"
    assert body["messages"][-1]["content"] == "new answer"
    assert body["messages"][-1]["reasoning"] == "updated reasoning"
    assert body["messages"][-1]["usage"] == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}

    persisted = client.get(f"/api/bots/{bot_id}/sessions/{session_id}")
    assert persisted.status_code == 200, persisted.text
    assert persisted.json()["messages"][-1]["message_id"] == "msg-assistant-new"


def test_dashboard_provider_models_and_chat_gating(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.minibot.server.manager.AgentManager._fetch_provider_model_names",
        lambda self, **kwargs: asyncio.sleep(0, result=["gpt-4.1-mini", "gpt-4.1"]),
    )

    provider = client.post(
        "/api/providers",
        json={
            "name": "OpenAI Compatible",
            "base_url": "https://example.invalid/v1",
            "api_key": "secret-key",
        },
    )
    assert provider.status_code == 200, provider.text
    provider_id = provider.json()["provider_id"]

    fetched = client.post(f"/api/providers/{provider_id}/fetch-models", json={})
    assert fetched.status_code == 200, fetched.text
    fetched_names = [item["model_name"] for item in fetched.json()]
    assert fetched_names == ["gpt-4.1", "gpt-4.1-mini"]

    imported = client.post(
        f"/api/providers/{provider_id}/models",
        json={"model_names": ["gpt-4.1-mini"], "added_via": "fetched"},
    )
    assert imported.status_code == 200, imported.text
    model_id = imported.json()[0]["model_id"]

    bot_id = client.post("/api/bots", json={"name": "Model Bot"}).json()["bot_id"]
    put = client.put(
        f"/api/bots/{bot_id}/config",
        json={"chat_model_id": model_id},
    )
    assert put.status_code == 200, put.text

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert any(item["provider_id"] == provider_id for item in body["providers"])
    assert any(item["model_id"] == model_id for item in body["models"])
    assert any(item["model_id"] == model_id for item in body["available_models"])

    cfg = client.get(f"/api/bots/{bot_id}/config").json()
    assert cfg["chat_model_id"] == model_id
    assert cfg["chat_ready"] is True

    disabled = client.put(f"/api/providers/{provider_id}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text

    cfg2 = client.get(f"/api/bots/{bot_id}/config").json()
    assert cfg2["chat_ready"] is False
    assert "disabled" in cfg2["chat_disabled_reason"].lower()

    rejected = client.post(f"/api/bots/{bot_id}/chat", json={"prompt": "hello"})
    assert rejected.status_code == 400


def test_chat_and_stream_endpoints_forward_reasoning_effort(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_id = client.post("/api/bots", json={"name": "Reasoning Bot"}).json()["bot_id"]
    seen: dict[str, object] = {}

    async def fake_get_bot(self, requested_bot_id: str, requested_session_id: str):
        assert requested_bot_id == bot_id

        class FakeBot:
            async def chat(
                self,
                prompt: str,
                *,
                session_id: str | None = None,
                reasoning_effort: str | None = None,
            ):
                seen["chat"] = {
                    "prompt": prompt,
                    "session_id": session_id,
                    "reasoning_effort": reasoning_effort,
                }
                return SimpleNamespace(
                    session_id=requested_session_id,
                    assistant_text="ok",
                    messages=[{"role": "assistant", "content": "ok", "message_id": "msg-assistant-1"}],
                    usage=None,
                )

            async def stream(
                self,
                prompt: str,
                *,
                session_id: str | None = None,
                reasoning_effort: str | None = None,
            ):
                seen["stream"] = {
                    "prompt": prompt,
                    "session_id": session_id,
                    "reasoning_effort": reasoning_effort,
                }
                yield {
                    "type": "assistant_end",
                    "message_id": "msg-assistant-2",
                    "content": "stream-ok",
                    "reasoning": "step by step",
                }

            def cancel(self) -> None:
                return None

        return FakeBot()

    monkeypatch.setattr("src.minibot.server.manager.AgentManager.get_bot", fake_get_bot)

    chat = client.post(
        f"/api/bots/{bot_id}/chat",
        json={"session_id": "sess-chat", "prompt": "hello", "reasoning_effort": "high"},
    )
    assert chat.status_code == 200, chat.text
    assert seen["chat"] == {
        "prompt": "hello",
        "session_id": "sess-chat",
        "reasoning_effort": "high",
    }

    stream = client.post(
        f"/api/bots/{bot_id}/stream",
        json={"session_id": "sess-stream", "prompt": "hello stream", "reasoning_effort": "low"},
    )
    assert stream.status_code == 200, stream.text
    assert seen["stream"] == {
        "prompt": "hello stream",
        "session_id": "sess-stream",
        "reasoning_effort": "low",
    }


def test_provider_models_can_be_deleted_in_batch(client: TestClient) -> None:
    provider_id = client.post(
        "/api/providers",
        json={
            "name": "Batch Delete Provider",
            "base_url": "https://example.invalid/v1",
            "api_key": "secret-key",
        },
    ).json()["provider_id"]

    imported = client.post(
        f"/api/providers/{provider_id}/models",
        json={"model_names": ["gpt-a", "gpt-b"], "added_via": "manual"},
    )
    assert imported.status_code == 200, imported.text
    imported_models = imported.json()
    model_a = imported_models[0]["model_id"]
    model_b = imported_models[1]["model_id"]

    bot_id = client.post("/api/bots", json={"name": "Delete Model Bot"}).json()["bot_id"]
    put = client.put(
        f"/api/bots/{bot_id}/config",
        json={"chat_model_id": model_a},
    )
    assert put.status_code == 200, put.text

    deleted = client.post(
        f"/api/providers/{provider_id}/models/delete",
        json={"model_ids": [model_a, model_b]},
    )
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted_count"] == 2
    assert set(body["deleted_model_ids"]) == {model_a, model_b}

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    remaining_ids = {item["model_id"] for item in dashboard.json()["models"]}
    assert model_a not in remaining_ids
    assert model_b not in remaining_ids

    cfg = client.get(f"/api/bots/{bot_id}/config").json()
    assert cfg["chat_ready"] is False
    assert "unavailable" in cfg["chat_disabled_reason"].lower()


def test_bot_subagent_candidates_and_cleanup(client: TestClient, server_env: dict[str, Path]) -> None:
    worker_id = client.post("/api/bots", json={"name": "Worker"}).json()["bot_id"]
    owner_id = client.post("/api/bots", json={"name": "Owner"}).json()["bot_id"]

    put_worker = client.put(
        f"/api/bots/{worker_id}/config",
        json={
            "subagent_exposable": True,
            "subagent_name": "Research Worker",
            "subagent_description": "Collects context before implementation.",
        },
    )
    assert put_worker.status_code == 200, put_worker.text

    candidates = client.get(f"/api/bots/{owner_id}/subagent-candidates")
    assert candidates.status_code == 200, candidates.text
    candidate_ids = {item["bot_id"] for item in candidates.json()}
    assert worker_id in candidate_ids

    attached = client.put(
        f"/api/bots/{owner_id}/config",
        json={"attached_subagent_bot_ids": [worker_id]},
    )
    assert attached.status_code == 200, attached.text

    cfg = client.get(f"/api/bots/{owner_id}/config").json()
    assert cfg["attached_subagent_bot_ids"] == [worker_id]

    from src.minibot.server.manager import AgentManager

    manager = AgentManager(workdir=server_env["workdir"])
    sid = manager.sessions_for(owner_id).create()
    bot = asyncio.run(manager.get_bot(owner_id, sid))
    agent_name = f"bot_{worker_id}"
    assert bot.agent.agent_registry.get(agent_name) is not None
    tool_names = {item["function"]["name"] for item in bot.agent.subagent_executor._get_tools_for_agent(agent_name)}
    assert "Task" not in tool_names
    assert "TeamCreate" not in tool_names

    deleted = client.delete(f"/api/bots/{worker_id}")
    assert deleted.status_code == 200, deleted.text

    cfg_after = client.get(f"/api/bots/{owner_id}/config").json()
    assert cfg_after["attached_subagent_bot_ids"] == []
