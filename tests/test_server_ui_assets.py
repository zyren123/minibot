from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_resolve_static_dir_prefers_packaged_static_then_webui_dist(tmp_path: Path) -> None:
    from src.minibot.server.app import _resolve_static_dir

    repo_root = tmp_path / "repo"
    server_dir = repo_root / "src" / "minibot" / "server"
    static_dir = server_dir / "static"
    dist_dir = repo_root / "webui" / "dist"
    static_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    (static_dir / "index.html").write_text("packaged", encoding="utf-8")
    (dist_dir / "index.html").write_text("dist", encoding="utf-8")

    assert _resolve_static_dir(server_dir) == static_dir

    (static_dir / "index.html").unlink()

    assert _resolve_static_dir(server_dir) == dist_dir


def test_root_returns_build_hint_when_webui_assets_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.minibot.server.app import create_app

    home = tmp_path / "minibot_home"
    workdir = tmp_path / "workdir"
    home.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINIBOT_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr("src.minibot.server.app._resolve_static_dir", lambda _server_dir: None)

    client = TestClient(create_app(workdir=workdir))
    response = client.get("/")

    assert response.status_code == 503
    assert "Minibot WebUI is not built" in response.text
    assert "npm run build" in response.text
