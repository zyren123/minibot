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
