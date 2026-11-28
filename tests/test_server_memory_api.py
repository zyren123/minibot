import asyncio
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.minibot.memory.manager import MemoryManager
from src.minibot.memory.repository import MemoryRepository


@pytest.fixture()
def server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "minibot_home"
    workdir = tmp_path / "workdir"
    home.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINIBOT_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    for key in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    return {"home": home, "workdir": workdir}


@pytest.fixture()
def client(server_env: dict[str, Path]) -> TestClient:
    from src.minibot.server.app import create_app

    return TestClient(create_app(workdir=server_env["workdir"]))


def test_bot_memory_endpoints_list_create_namespace_and_search(client: TestClient) -> None:
    created = client.post(
        "/api/bots/default/memory/namespaces",
        json={"slug": "ember-falls", "title": "Ember Falls"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["slug"] == "ember-falls"

    namespaces = client.get("/api/bots/default/memory/namespaces")
    assert namespaces.status_code == 200, namespaces.text
    assert namespaces.json()[0]["slug"] == "ember-falls"

    root_folder = client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "characters", "title": "Characters", "kind": "folder"},
    )
    assert root_folder.status_code == 200, root_folder.text

    ali_node = client.post(
        "/api/bots/default/memory/nodes",
        json={
            "parent_uri": "memory://characters",
            "slug": "ali",
            "title": "Ali",
            "kind": "memory",
            "node_type": "character",
            "content": "POV protagonist",
        },
    )
    assert ali_node.status_code == 200, ali_node.text

    search = client.get("/api/bots/default/memory/search", params={"query": "Ali"})
    assert search.status_code == 200, search.text
    assert search.json()[0]["uri"] == "memory://characters/ali"


def test_updating_bot_config_switches_active_memory_namespace(client: TestClient) -> None:
    created = client.post(
        "/api/bots/default/memory/namespaces",
        json={"slug": "ember-falls", "title": "Ember Falls"},
    )
    assert created.status_code == 200, created.text

    resp = client.put("/api/bots/default/config", json={"active_memory_namespace": "ember-falls"})
    assert resp.status_code == 200, resp.text

    cfg = client.get("/api/bots/default/config")
    assert cfg.status_code == 200, cfg.text
    assert cfg.json()["active_memory_namespace"] == "ember-falls"


def test_memory_tree_graph_and_node_endpoints_return_structured_payloads(client: TestClient) -> None:
    client.post("/api/bots/default/memory/namespaces", json={"slug": "ember-falls", "title": "Ember Falls"})
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "characters", "title": "Characters", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={
            "parent_uri": "memory://characters",
            "slug": "ali",
            "title": "Ali",
            "kind": "memory",
            "node_type": "character",
            "content": "POV protagonist",
        },
    )

    tree = client.get("/api/bots/default/memory/tree")
    assert tree.status_code == 200, tree.text
    assert tree.json()["nodes"][0]["uri"] == "memory://characters"

    graph = client.get("/api/bots/default/memory/graph")
    assert graph.status_code == 200, graph.text
    assert graph.json()["edges"][0]["source"] == "memory://characters"

    node = client.get("/api/bots/default/memory/node", params={"uri": "memory://characters/ali"})
    assert node.status_code == 200, node.text
    assert node.json()["uri"] == "memory://characters/ali"


def test_memory_tree_endpoint_avoids_recursive_list_children_reads(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.post("/api/bots/default/memory/namespaces", json={"slug": "ember-falls", "title": "Ember Falls"})
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "characters", "title": "Characters", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "locations", "title": "Locations", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={
            "parent_uri": "memory://characters",
            "slug": "ali",
            "title": "Ali",
            "kind": "memory",
            "node_type": "character",
            "content": "POV protagonist",
        },
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={
            "parent_uri": "memory://locations",
            "slug": "bailu-town",
            "title": "Bailu Town",
            "kind": "memory",
            "node_type": "location",
            "content": "Riverside town",
        },
    )

    calls = {"count": 0}
    original = MemoryRepository.list_children

    def counted(self, namespace_slug: str, parent_uri: str | None = None):
        calls["count"] += 1
        return original(self, namespace_slug, parent_uri)

    monkeypatch.setattr(MemoryRepository, "list_children", counted)

    tree = client.get("/api/bots/default/memory/tree")

    assert tree.status_code == 200, tree.text
    assert calls["count"] == 0


WORKSPACE = "/api/bots/default/memory/workspace"


def test_file_memory_workspace_edits_markdown_and_rejects_stale_revision(client, server_env):
    assert client.get(f"{WORKSPACE}/config").json() == {
        "provider": "file", "vault_path": None, "connection": "vault", "mcp_url": None,
    }
    root = client.get(WORKSPACE, params={"provider": "file"}).json()
    assert [entry["id"] for entry in root["entries"]] == ["LONG_TERM.md", "daily"]

    document_url = f"{WORKSPACE}/document"
    path = "LONG_TERM.md"
    original = client.get(document_url, params={"provider": "file", "path": path}).json()
    change = {"provider": "file", "path": path, "revision": original["revision"], "content": "长期约定\n"}
    assert client.put(document_url, json=change).status_code == 200
    assert (server_env["home"] / "memory" / path).read_text(encoding="utf-8") == "长期约定\n"
    assert client.put(document_url, json=change).status_code == 409

    today = f"daily/{date.today().isoformat()}.md"
    days = client.get(WORKSPACE, params={"provider": "file", "path": "daily"}).json()["entries"]
    assert days[0]["id"] == today
    new_day = client.get(document_url, params={"provider": "file", "path": today}).json()
    assert client.put(document_url, json={
        **change, "path": today, "revision": new_day["revision"], "content": "每日记录\n",
    }).status_code == 200
    assert (server_env["home"] / "memory" / today).read_text(encoding="utf-8") == "每日记录\n"


def test_memory_workspace_switch_validates_vault_and_persists_per_bot(client, server_env):
    config_url = f"{WORKSPACE}/config"
    invalid = client.put(config_url, json={"provider": "novelmemo", "vault_path": str(server_env["workdir"])})
    assert invalid.status_code == 400
    assert client.get(config_url).json()["provider"] == "file"

    vault = server_env["workdir"] / "雾灯港"
    metadata = vault / ".memory"
    metadata.mkdir(parents=True)
    (metadata / "memory.yaml").write_text("locale: zh-CN\nvalid_domains: [core]\n", encoding="utf-8")
    note = vault / "core" / "人物" / "守灯人.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ndisclosure: 描写西堤时读取\ntriggers: [旧地图]\n---\n守灯人的旧记忆。\n", encoding="utf-8")
    selected = client.put(config_url, json={"provider": "novelmemo", "vault_path": str(vault)})
    assert selected.status_code == 200, selected.text
    assert selected.json() == {
        "provider": "novelmemo", "vault_path": str(vault.resolve()), "connection": "vault", "mcp_url": None,
    }
    another = client.post("/api/bots", json={"name": "另一个 Bot"}).json()["bot_id"]
    assert client.get(f"/api/bots/{another}/memory/workspace/config").json()["provider"] == "file"

    folder = client.get(WORKSPACE, params={"provider": "novelmemo", "path": "core/人物"}).json()
    assert folder["breadcrumbs"][-1]["title"] == "人物"
    assert folder["entries"][0]["title"] == "守灯人"
    document_url = f"{WORKSPACE}/document"
    document_id = folder["entries"][0]["id"]
    original = client.get(document_url, params={"provider": "novelmemo", "path": document_id}).json()
    assert original["triggers"] == ["旧地图"]
    edited = client.put(document_url, json={
        "provider": "novelmemo", "path": document_id, "revision": original["revision"],
        "content": "更新后的守灯人记忆。", "disclosure": '阅读时机: "第二章"', "triggers": ["夜航信标"],
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["triggers"] == ["夜航信标"]
    assert "更新后的守灯人记忆。" in note.read_text(encoding="utf-8")
    matches = client.get(f"{WORKSPACE}/search", params={"provider": "novelmemo", "query": "夜航信标"})
    assert matches.status_code == 200, matches.text
    assert any(entry["id"] == document_id for entry in matches.json())
    assert client.get(f"{WORKSPACE}/graph", params={"provider": "novelmemo"}).status_code == 200

    restored = client.put(config_url, json={"provider": "file"})
    assert restored.json() == {**selected.json(), "provider": "file"}
    assert client.get(document_url, params={"provider": "novelmemo", "path": document_id}).status_code == 409
    assert client.put(config_url, json={"provider": "novelmemo"}).status_code == 200


def test_mcp_memory_workspace_checks_compatibility_and_remembers_selection(client, monkeypatch):
    from src.minibot.memory.remote_novelmemo import RemoteNovelMemoIncompatible, RemoteNovelMemoWorkspace

    url = "https://memory.example.invalid/mcp"

    async def validate(workspace):
        if workspace.url != url:
            raise RemoteNovelMemoIncompatible("Missing Novel Memo tools")

    async def directory(workspace, path):
        assert workspace.url == url
        assert path == ""
        return {"entries": [], "breadcrumbs": [{"id": "", "title": "Remote vault"}]}

    monkeypatch.setattr(RemoteNovelMemoWorkspace, "validate", validate)
    monkeypatch.setattr(RemoteNovelMemoWorkspace, "directory", directory)

    choice = {"provider": "novelmemo", "connection": "mcp"}
    config_url = f"{WORKSPACE}/config"
    rejected = client.put(config_url, json={**choice, "mcp_url": "https://wrong.example.invalid/mcp"})
    assert rejected.status_code == 400, rejected.text
    assert client.get(config_url).json()["provider"] == "file"
    assert client.put(config_url, json=choice).status_code == 400

    selected = client.put(config_url, json={**choice, "mcp_url": url})
    assert selected.status_code == 200, selected.text
    assert selected.json() == {**choice, "mcp_url": url, "vault_path": None}
    root = client.get(WORKSPACE, params={"provider": "novelmemo"})
    assert root.json()["breadcrumbs"][-1]["title"] == "Remote vault"

    other_bot = client.post("/api/bots", json={"name": "Local"}).json()["bot_id"]
    assert client.get(f"/api/bots/{other_bot}/memory/workspace/config").json()["provider"] == "file"
    assert client.put(config_url, json={"provider": "file"}).json() == {**selected.json(), "provider": "file"}
    assert client.put(config_url, json={"provider": "novelmemo"}).json() == selected.json()


@pytest.mark.asyncio
async def test_mcp_workspace_switch_rechecks_chat_lock_after_validation(server_env, monkeypatch):
    from src.minibot.memory.remote_novelmemo import RemoteNovelMemoWorkspace
    from src.minibot.server.manager import AgentManager, MemoryWorkspaceChanged

    manager = AgentManager(workdir=server_env["workdir"])
    started, finish = asyncio.Event(), asyncio.Event()
    url = "https://memory.example.invalid/mcp"

    async def validate(workspace):
        assert workspace.url == url
        started.set()
        await finish.wait()

    monkeypatch.setattr(RemoteNovelMemoWorkspace, "validate", validate)
    switch = asyncio.create_task(
        manager.configure_memory_workspace("default", "novelmemo", connection="mcp", mcp_url=url)
    )
    try:
        async with asyncio.timeout(5):
            await started.wait()
            lock = await manager.session_lock("default", "existing")
            async with lock:
                finish.set()
                with pytest.raises(MemoryWorkspaceChanged):
                    await switch
    finally:
        if not switch.done():
            switch.cancel()
            await asyncio.gather(switch, return_exceptions=True)
    assert manager.memory_workspace_config("default")["provider"] == "file"


@pytest.mark.asyncio
async def test_remote_novelmemo_is_the_agents_active_memory(server_env, monkeypatch):
    from src.minibot.memory.remote_novelmemo import RemoteNovelMemoWorkspace
    from src.minibot.server.manager import AgentManager

    url = "https://memory.example.invalid/mcp"
    manager = AgentManager(workdir=server_env["workdir"])
    manager._base_config.mcp.enabled = False

    async def validate(workspace):
        assert workspace.url == url

    async def read_memory(workspace, uri):
        assert workspace.url == url
        assert uri == "core://project"
        return "来自远程的项目记录。"

    monkeypatch.setattr(RemoteNovelMemoWorkspace, "validate", validate)
    monkeypatch.setattr(RemoteNovelMemoWorkspace, "read_memory", read_memory)
    session = manager.sessions_for("default").create()
    old_bot = await manager.get_bot("default", session)
    assert old_bot.agent.tool_registry.get("memory_read") is not None

    await manager.configure_memory_workspace("default", "novelmemo", connection="mcp", mcp_url=url)
    bot = await manager.get_bot("default", session)
    assert bot is not old_bot
    assert bot.agent.tool_registry.get("memory_read") is None
    assert isinstance(bot.agent.memory_workspace, RemoteNovelMemoWorkspace)
    assert "`system://boot`" in bot.agent.system_prompt
    outcome = await bot.agent.tool_registry.execute_result("read_memory", {"uri": "core://project"})
    assert not outcome.is_error and outcome.text == "来自远程的项目记录。"


@pytest.mark.asyncio
async def test_switching_memory_rebuilds_the_agent_against_the_same_vault(server_env):
    from src.minibot.server.manager import AgentManager, MemoryWorkspaceChanged

    manager = AgentManager(workdir=server_env["workdir"])
    manager._base_config.mcp.enabled = False
    vault = server_env["workdir"] / "vault"
    manifest = vault / ".memory" / "memory.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("locale: zh-CN\nvalid_domains: [core]\n", encoding="utf-8")
    note = vault / "core" / "project.md"
    note.parent.mkdir()
    note.write_text("---\ndisclosure: 项目背景\n---\n原始约定。\n", encoding="utf-8")

    session = manager.sessions_for("default").create()
    file_bot = await manager.get_bot("default", session)
    assert file_bot.agent.tool_registry.get("memory_read") is not None
    lock = await manager.session_lock("default", session)
    async with lock:
        with pytest.raises(MemoryWorkspaceChanged):
            await manager.configure_memory_workspace("default", "novelmemo", str(vault))
        assert manager.memory_workspace_config("default")["provider"] == "file"

    await manager.configure_memory_workspace("default", "novelmemo", str(vault))
    assert await manager.session_lock("default", session) is lock
    novel_bot = await manager.get_bot("default", session)
    assert novel_bot is not file_bot
    assert novel_bot.agent.tool_registry.get("memory_read") is None
    assert novel_bot.agent.memory_workspace is manager.memory_workspace("default", "novelmemo")

    current = manager.memory_workspace("default", "novelmemo").document("core/project.md")
    await manager.save_memory_workspace_document(
        "default", "novelmemo", current["id"], "从网页更新的约定。", current["revision"],
    )
    outcome = await novel_bot.agent.tool_registry.execute_result("read_memory", {"uri": "core://project"})
    assert not outcome.is_error and "从网页更新的约定" in outcome.text


def test_memory_node_endpoint_does_not_reenter_generic_read_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.post("/api/bots/default/memory/namespaces", json={"slug": "ember-falls", "title": "Ember Falls"})
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "characters", "title": "Characters", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={
            "parent_uri": "memory://characters",
            "slug": "ali",
            "title": "Ali",
            "kind": "memory",
            "node_type": "character",
            "content": "POV protagonist",
        },
    )

    calls = {"count": 0}
    original = MemoryManager.read

    def counted(self, uri: str):
        calls["count"] += 1
        return original(self, uri)

    monkeypatch.setattr(MemoryManager, "read", counted)

    node = client.get("/api/bots/default/memory/node", params={"uri": "memory://characters/ali"})

    assert node.status_code == 200, node.text
    assert calls["count"] == 0


def test_memory_node_update_endpoint_edits_title_and_content(client: TestClient) -> None:
    client.post("/api/bots/default/memory/namespaces", json={"slug": "ember-falls", "title": "Ember Falls"})
    client.post(
        "/api/bots/default/memory/nodes",
        json={
            "parent_uri": None,
            "slug": "furina-persona",
            "title": "Furina Persona",
            "kind": "memory",
            "content": "Before",
        },
    )

    updated = client.put(
        "/api/bots/default/memory/node",
        json={"uri": "memory://furina-persona", "title": "Furina Core Persona", "content": "After"},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Furina Core Persona"

    node = client.get("/api/bots/default/memory/node", params={"uri": "memory://furina-persona"})
    assert node.status_code == 200, node.text
    assert node.json()["title"] == "Furina Core Persona"
    assert node.json()["content"] == "After"


def test_memory_node_delete_endpoint_removes_subtree(client: TestClient) -> None:
    client.post("/api/bots/default/memory/namespaces", json={"slug": "ember-falls", "title": "Ember Falls"})
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "characters", "title": "Characters", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": "memory://characters", "slug": "ali", "title": "Ali", "kind": "memory", "content": "A"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": "memory://characters/ali", "slug": "notes", "title": "Notes", "kind": "memory", "content": "B"},
    )

    deleted = client.request("DELETE", "/api/bots/default/memory/node", json={"uri": "memory://characters"})

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True

    tree = client.get("/api/bots/default/memory/tree")
    assert tree.status_code == 200, tree.text
    assert tree.json()["nodes"] == []

    node = client.get("/api/bots/default/memory/node", params={"uri": "memory://characters/ali"})
    assert node.status_code == 404, node.text


def test_memory_batch_delete_endpoint_removes_multiple_subtrees(client: TestClient) -> None:
    client.post("/api/bots/default/memory/namespaces", json={"slug": "ember-falls", "title": "Ember Falls"})
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "characters", "title": "Characters", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": None, "slug": "locations", "title": "Locations", "kind": "folder"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": "memory://characters", "slug": "ali", "title": "Ali", "kind": "memory", "content": "A"},
    )
    client.post(
        "/api/bots/default/memory/nodes",
        json={"parent_uri": "memory://locations", "slug": "bailu-town", "title": "Bailu Town", "kind": "memory", "content": "B"},
    )

    deleted = client.request(
        "DELETE",
        "/api/bots/default/memory/nodes",
        json={"uris": ["memory://characters", "memory://locations"]},
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_count"] == 2

    tree = client.get("/api/bots/default/memory/tree")
    assert tree.status_code == 200, tree.text
    assert tree.json()["nodes"] == []
