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
