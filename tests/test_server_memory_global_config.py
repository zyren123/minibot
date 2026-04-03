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


def test_global_config_snapshot_exposes_memory_backend_and_url_state(client: TestClient) -> None:
    data = client.get("/api/config").json()
    assert data["memory_backend"] == "sqlite"
    assert data["memory_database_url_configured"] is False
    assert data["memory_database_url_value"] is None


def test_updating_global_config_persists_memory_backend_and_env_url(
    client: TestClient,
    server_env: dict[str, Path],
) -> None:
    put = client.put(
        "/api/config",
        json={
            "memory_backend": "postgres",
            "memory_database_url_value": "postgresql://writer:secret@localhost:5432/minibot_memory",
        },
    )
    assert put.status_code == 200, put.text

    config_yaml = (server_env["home"] / "config" / "default.yaml").read_text(encoding="utf-8")
    dotenv_text = (server_env["home"] / ".env").read_text(encoding="utf-8")

    assert "backend: postgres" in config_yaml
    assert "${MEMORY_DATABASE_URL}" in config_yaml
    assert "postgresql://writer:secret@localhost:5432/minibot_memory" in dotenv_text
