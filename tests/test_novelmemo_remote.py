from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from src.minibot.memory.novelmemo_mcp import create_novelmemo_mcp
from src.minibot.memory.remote_novelmemo import (
    RemoteNovelMemoIncompatible,
    RemoteNovelMemoUnavailable,
    RemoteNovelMemoWorkspace,
)
from src.minibot.memory.workspace import MemoryRevisionConflict, open_novelmemo_workspace


def _transport_to(monkeypatch, app):
    create_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=app)

    def client(**options):
        return create_client(transport=transport, **options)

    monkeypatch.setattr("src.minibot.mcp.streamable_http.httpx.AsyncClient", client)


def _vault(root: Path) -> Path:
    config = root / ".memory" / "memory.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("valid_domains: [writer]\nboot_uris: {}\n", encoding="utf-8")
    note = root / "writer" / "人物" / "宁宁.md"
    note.parent.mkdir(parents=True)
    (note.parent / "_index.md").write_text("---\ndisclosure: 人物目录\n---\n", encoding="utf-8")
    note.write_text('---\ndisclosure: "人物: 宁宁"\n---\n\n旧笔记\n', encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_remote_workspace_uses_same_vault_for_ui_and_agent(monkeypatch, tmp_path):
    vault = open_novelmemo_workspace(_vault(tmp_path / "写作资料"))
    app = create_novelmemo_mcp(vault).streamable_http_app()
    remote = RemoteNovelMemoWorkspace(" http://localhost:8767/mcp ")
    assert remote.url == "http://localhost:8767/mcp"
    _transport_to(monkeypatch, app)

    async with app.router.lifespan_context(app):
        await remote.validate()
        assert (await remote.directory(""))["entries"][0]["id"] == "writer"
        note = "writer/人物/宁宁.md"
        uri = "writer://人物/宁宁"
        assert (await remote.directory("writer/人物"))["entries"][0]["id"] == note
        assert note in {entry["id"] for entry in await remote.graph()}
        with pytest.raises(FileNotFoundError):
            await remote.document("writer/人物/不存在.md")
        with pytest.raises(ValueError, match="rejected"):
            await remote.directory("../")

        original = await remote.document(note)
        saved = await remote.save_document(note, "新的中文笔记", original["revision"], triggers=["台词"])
        assert saved["triggers"] == ["台词"]
        assert "新的中文笔记" in await remote.read_memory(uri)
        assert (await remote.search("台词"))[0]["id"] == note
        assert (await remote.search_memory("台词"))[0]["uri"] == uri
        assert await remote.manage_triggers(uri, add=["续集"]) == ["台词", "续集"]

        with pytest.raises(MemoryRevisionConflict):
            await remote.save_document(note, "过期内容", saved["revision"])
        other = RemoteNovelMemoWorkspace("http://127.0.0.1:8767/mcp")
        with pytest.raises(MemoryRevisionConflict, match="source changed"):
            await other.save_document(note, "其他端点的编辑", (await remote.document(note))["revision"])

        assert await remote.update_memory(uri, append="。") == uri
        child = await remote.create_memory("writer://人物", "中文新记忆", "故事角色", "小宇")
        assert "中文新记忆" in await remote.read_memory(child)
        assert await remote.delete_memory(child) == child
        assert "旧笔记" not in vault.document(note)["content"]


@pytest.mark.asyncio
async def test_remote_activation_checks_capabilities_and_connection_failures(monkeypatch):
    server = FastMCP("Unrelated server", stateless_http=True, json_response=True)

    @server.tool()
    def read_memory(uri: str) -> str:
        return uri

    app = server.streamable_http_app()
    _transport_to(monkeypatch, app)
    async with app.router.lifespan_context(app):
        with pytest.raises(RemoteNovelMemoIncompatible, match="workspace tools"):
            await RemoteNovelMemoWorkspace("http://127.0.0.1:8767/mcp").validate()
        with pytest.raises(RemoteNovelMemoUnavailable, match="Could not connect"):
            await RemoteNovelMemoWorkspace("http://127.0.0.1:8767/wrong-path").validate()


@pytest.mark.asyncio
async def test_local_companion_checks_host_and_origin(tmp_path):
    vault = open_novelmemo_workspace(_vault(tmp_path / "写作资料"))
    app = create_novelmemo_mcp(vault).streamable_http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            endpoint = "http://127.0.0.1:8767/mcp"
            bad_host = await client.post(endpoint, json={}, headers={"Host": "untrusted.example"})
            bad_origin = await client.post(endpoint, json={}, headers={"Origin": "https://untrusted.example"})
    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403


@pytest.mark.parametrize("url", [
    "127.0.0.1:8767/mcp", "file:///tmp/mcp", "https://user:key@example.org/mcp",
    "https://example.org/mcp?token=private", "https://example.org/mcp#fragment",
])
def test_remote_endpoint_rejects_unsupported_urls(url):
    with pytest.raises(ValueError, match="absolute HTTP or HTTPS"):
        RemoteNovelMemoWorkspace(url)
