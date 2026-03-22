import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.minibot.server.platform_runtime import FeishuPlatformRuntime
from src.minibot.server.platforms import ConversationMapStore


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


def test_platform_crud_and_dashboard_snapshot(client: TestClient) -> None:
    bot = client.post("/api/bots", json={"name": "Bound Bot"})
    assert bot.status_code == 200, bot.text
    bot_id = bot.json()["bot_id"]

    created = client.post(
        "/api/platforms",
        json={
            "name": "Feishu Support",
            "kind": "feishu",
            "bound_bot_id": bot_id,
            "app_id": "cli_xxx",
            "app_secret": "secret-value",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    platform_id = body["platform_id"]

    assert body["kind"] == "feishu"
    assert body["mode"] == "websocket"
    assert body["scope"] == "private"
    assert body["bound_bot_id"] == bot_id
    assert body["bound_bot_name"] == "Bound Bot"
    assert body["app_secret_masked"]

    listed = client.get("/api/platforms")
    assert listed.status_code == 200, listed.text
    assert any(item["platform_id"] == platform_id for item in listed.json())

    detail = client.get(f"/api/platforms/{platform_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Feishu Support"

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    assert any(item["platform_id"] == platform_id for item in dashboard.json()["platforms"])


def test_platform_api_accepts_non_feishu_kinds(client: TestClient) -> None:
    created = client.post(
        "/api/platforms",
        json={
            "name": "Telegram Relay",
            "kind": "telegram",
            "bound_bot_id": "default",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["kind"] == "telegram"
    assert body["app_id"] == ""


def test_platform_update_rebinding_clears_conversation_map(
    client: TestClient,
    server_env: dict[str, Path],
) -> None:
    first_bot_id = client.post("/api/bots", json={"name": "First Bot"}).json()["bot_id"]
    second_bot_id = client.post("/api/bots", json={"name": "Second Bot"}).json()["bot_id"]
    platform_id = client.post(
        "/api/platforms",
        json={
            "name": "Feishu Team",
            "kind": "feishu",
            "bound_bot_id": first_bot_id,
            "app_id": "cli_one",
            "app_secret": "secret-one",
        },
    ).json()["platform_id"]

    conversation_store = ConversationMapStore(app_home=server_env["home"], platform_id=platform_id)
    conversation_store.set_mapping(
        external_chat_id="oc_chat_1",
        session_id="session-old",
        last_message_id="msg-1",
    )

    updated = client.put(
        f"/api/platforms/{platform_id}",
        json={"bound_bot_id": second_bot_id},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["bound_bot_id"] == second_bot_id
    assert conversation_store.get("oc_chat_1") is None


def test_delete_bound_bot_rebinds_platform_to_default_and_clears_mapping(
    client: TestClient,
    server_env: dict[str, Path],
) -> None:
    bot_id = client.post("/api/bots", json={"name": "Ephemeral Bot"}).json()["bot_id"]
    platform_id = client.post(
        "/api/platforms",
        json={
            "name": "Feishu Bridge",
            "kind": "feishu",
            "bound_bot_id": bot_id,
            "app_id": "cli_delete",
            "app_secret": "secret-delete",
        },
    ).json()["platform_id"]

    conversation_store = ConversationMapStore(app_home=server_env["home"], platform_id=platform_id)
    conversation_store.set_mapping(
        external_chat_id="oc_chat_delete",
        session_id="session-delete",
        last_message_id="msg-delete",
    )

    deleted = client.delete(f"/api/bots/{bot_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True

    detail = client.get(f"/api/platforms/{platform_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["bound_bot_id"] == "default"
    assert detail.json()["bound_bot_name"] == "Minibot"
    assert conversation_store.get("oc_chat_delete") is None


def test_manager_platform_runtime_lifecycle(server_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from src.minibot.server.manager import AgentManager

    manager = AgentManager(workdir=server_env["workdir"])
    seen: list[tuple[str, str]] = []

    class FakeRuntime:
        def __init__(self, platform_id: str) -> None:
            self.platform_id = platform_id

        async def start(self) -> None:
            seen.append(("start", self.platform_id))

        async def stop(self) -> None:
            seen.append(("stop", self.platform_id))

    monkeypatch.setattr(manager, "_build_platform_runtime", lambda platform: FakeRuntime(platform["platform_id"]))

    asyncio.run(manager.start_platform_runtimes())
    created = asyncio.run(
        manager.create_platform(
            {
                "name": "Feishu Runtime",
                "kind": "feishu",
                "bound_bot_id": "default",
                "app_id": "cli_runtime",
                "app_secret": "secret-runtime",
            }
        )
    )
    platform_id = created["platform_id"]
    assert ("start", platform_id) in seen

    asyncio.run(manager.update_platform(platform_id, {"enabled": False}))
    assert ("stop", platform_id) in seen

    asyncio.run(manager.update_platform(platform_id, {"enabled": True}))
    assert seen.count(("start", platform_id)) == 2

    deleted = asyncio.run(manager.delete_platform(platform_id))
    assert deleted is True
    assert seen.count(("stop", platform_id)) == 2

    asyncio.run(manager.stop_platform_runtimes())


def test_manager_allows_placeholder_platform_kinds(server_env: dict[str, Path]) -> None:
    from src.minibot.server.manager import AgentManager

    manager = AgentManager(workdir=server_env["workdir"])

    asyncio.run(manager.start_platform_runtimes())
    created = asyncio.run(
        manager.create_platform(
            {
                "name": "Telegram Queue",
                "kind": "telegram",
                "bound_bot_id": "default",
            }
        )
    )

    assert created["kind"] == "telegram"
    assert created["app_id"] == ""
    assert created["connected"] is False
    assert created["last_error"] == "Telegram runtime is not implemented yet."

    asyncio.run(manager.stop_platform_runtimes())


def test_feishu_runtime_routes_messages_and_deduplicates(tmp_path: Path) -> None:
    async def _run() -> None:
        sent: list[tuple[str, str]] = []

        class FakeSessionManager:
            def __init__(self) -> None:
                self._count = 0

            def create(self) -> str:
                self._count += 1
                return f"session-{self._count}"

        class FakeManager:
            def __init__(self) -> None:
                self.app_home = tmp_path
                self._lock = asyncio.Lock()
                self._sessions = {"default": FakeSessionManager()}
                self.state: dict[str, object] = {}

            def set_platform_runtime_state(self, _platform_id: str, **kwargs: object) -> None:
                self.state.update(kwargs)

            def bot_exists(self, _bot_id: str) -> bool:
                return True

            def sessions_for(self, bot_id: str) -> FakeSessionManager:
                return self._sessions.setdefault(bot_id, FakeSessionManager())

            async def session_lock(self, _bot_id: str, _session_id: str) -> asyncio.Lock:
                return self._lock

            async def get_bot(self, _bot_id: str, _session_id: str):
                class FakeBot:
                    async def chat(self, prompt: str, *, session_id: str | None = None):
                        assert prompt == "hello from feishu"
                        assert session_id == "session-1"
                        return SimpleNamespace(assistant_text="assistant reply")

                return FakeBot()

        manager = FakeManager()
        runtime = FeishuPlatformRuntime(
            manager=manager,
            platform={
                "platform_id": "plat_test",
                "bound_bot_id": "default",
                "app_id": "cli_xxx",
                "app_secret": "secret",
            },
        )

        async def fake_send(chat_id: str, text: str) -> None:
            sent.append((chat_id, text))

        runtime._send_text = fake_send  # type: ignore[method-assign]

        payload = {
            "event": {
                "sender": {"sender_type": "user"},
                "message": {
                    "chat_type": "p2p",
                    "chat_id": "oc_chat_1",
                    "message_id": "msg_1",
                    "message_type": "text",
                    "content": '{"text":"hello from feishu"}',
                },
            }
        }

        await runtime._handle_message_payload(payload)
        await runtime._handle_message_payload(payload)

        assert sent == [("oc_chat_1", "assistant reply")]
        conversation_store = ConversationMapStore(app_home=tmp_path, platform_id="plat_test")
        entry = conversation_store.get("oc_chat_1")
        assert entry is not None
        assert entry["external_chat_id"] == "oc_chat_1"
        assert entry["session_id"] == "session-1"
        assert entry["last_message_id"] == "msg_1"
        assert isinstance(entry["updated_at"], str) and entry["updated_at"]

    asyncio.run(_run())


def test_feishu_runtime_rejects_non_text_messages(tmp_path: Path) -> None:
    async def _run() -> None:
        sent: list[tuple[str, str]] = []

        class FakeSessionManager:
            def __init__(self) -> None:
                self._count = 0

            def create(self) -> str:
                self._count += 1
                return f"session-{self._count}"

        class FakeManager:
            def __init__(self) -> None:
                self.app_home = tmp_path
                self._lock = asyncio.Lock()
                self._sessions = {"default": FakeSessionManager()}

            def set_platform_runtime_state(self, _platform_id: str, **_kwargs: object) -> None:
                return None

            def bot_exists(self, _bot_id: str) -> bool:
                return True

            def sessions_for(self, bot_id: str) -> FakeSessionManager:
                return self._sessions.setdefault(bot_id, FakeSessionManager())

            async def session_lock(self, _bot_id: str, _session_id: str) -> asyncio.Lock:
                return self._lock

            async def get_bot(self, _bot_id: str, _session_id: str):
                raise AssertionError("non-text messages should not call chat")

        runtime = FeishuPlatformRuntime(
            manager=FakeManager(),
            platform={
                "platform_id": "plat_non_text",
                "bound_bot_id": "default",
                "app_id": "cli_xxx",
                "app_secret": "secret",
            },
        )

        async def fake_send(chat_id: str, text: str) -> None:
            sent.append((chat_id, text))

        runtime._send_text = fake_send  # type: ignore[method-assign]

        payload = {
            "event": {
                "sender": {"sender_type": "user"},
                "message": {
                    "chat_type": "p2p",
                    "chat_id": "oc_chat_non_text",
                    "message_id": "msg_non_text",
                    "message_type": "image",
                    "content": '{"image_key":"img"}',
                },
            }
        }

        await runtime._handle_message_payload(payload)

        assert sent == [("oc_chat_non_text", "This Feishu bridge currently supports text messages only.")]

    asyncio.run(_run())
