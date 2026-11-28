from datetime import date
from types import SimpleNamespace

import pytest
from src.minibot.agent import Agent
from src.minibot.config.schema import (
    Config,
    HooksConfig,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    MemoryConfig,
    SessionConfig,
    TeamsConfig,
    ToolsConfig,
)


@pytest.fixture(autouse=True)
def clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _config(tmp_path, *, memory_provider: str | None = None, vault_path: str | None = None):
    memory = MemoryConfig(enabled=True, memory_dir=str(tmp_path / "memory"))
    if memory_provider is not None:
        memory.provider = memory_provider
    memory.novelmemo_vault = vault_path
    return Config(
        workdir=tmp_path,
        app_home=tmp_path / ".minibot",
        project_root=tmp_path,
        llm=LLMConfig(
            base_url="http://localhost:8000/v1",
            api_key="test",
            model="test-model",
            stream_enabled=False,
        ),
        tools=ToolsConfig(),
        hooks=HooksConfig(enabled=False),
        mcp=MCPConfig(enabled=False),
        memory=memory,
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=False),
    )


def test_memory_tools_register_crud_search_and_trigger_management(tmp_path):
    agent = Agent(config=_config(tmp_path, memory_provider="structured"))

    names = {tool.name for tool in agent.tool_registry.get_all()}
    assert {
        "create_memory",
        "read_memory",
        "update_memory",
        "edit_memory",
        "delete_memory",
        "search_memory",
        "manage_triggers",
    } <= names
    assert "memory_read" not in names
    assert "memory_write" not in names


@pytest.mark.asyncio
async def test_default_agent_reads_and_writes_the_shared_file_memory(tmp_path):
    cfg = _config(tmp_path)
    cfg.mcp.servers.append(MCPServerConfig(name="novelmemo", command="unused"))
    agent = Agent(config=cfg)
    assert agent.mcp_manager.config.servers == []
    agent.silent = True
    tools = agent.tool_registry
    assert {"memory_read", "memory_write", "memory_list_daily"} <= {tool.name for tool in tools.get_all()}
    assert "create_memory" not in tools and "manage_triggers" not in tools

    today = date.today().isoformat()
    daily = await tools.execute_result("memory_write", {
        "memory_type": "daily", "date": today, "content": "今日进展",
    })
    assert not daily.is_error
    assert (tmp_path / "memory" / "daily" / f"{today}.md").read_text(encoding="utf-8") == "今日进展"
    assert today in await tools.execute("memory_list_daily", {})

    workspace = agent.memory_workspace
    long_term = workspace.document("LONG_TERM.md")
    written = workspace.save_document("LONG_TERM.md", "长期偏好", long_term["revision"])
    assert await tools.execute("memory_read", {"memory_type": "long_term"}) == "长期偏好"

    prompts = []

    class Provider:
        async def create_message_async(self, **kwargs):
            prompts.append(kwargs["system"])
            message = SimpleNamespace(content="done", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    agent.client = Provider()
    await agent.run_loop([{"role": "user", "content": "开始"}])
    workspace.save_document("LONG_TERM.md", "更新后的偏好", written["revision"])
    await agent.run_loop([{"role": "user", "content": "继续"}])
    assert "长期偏好" in prompts[0] and "今日进展" in prompts[0]
    assert "更新后的偏好" in prompts[1] and "长期偏好" not in prompts[1]


@pytest.mark.asyncio
async def test_embedded_novelmemo_tools_use_the_shared_vault(tmp_path):
    vault = tmp_path / "Novel Vault"
    (vault / ".memory").mkdir(parents=True)
    (vault / ".memory" / "memory.yaml").write_text("valid_domains: [core]\nlocale: en\n", encoding="utf-8")
    cfg = _config(tmp_path, memory_provider="novelmemo", vault_path=str(vault))
    cfg.mcp.servers.append(MCPServerConfig(name="novelmemo", command="unused"))

    agent = Agent(config=cfg)
    tools = agent.tool_registry
    assert "memory_write" not in tools and "edit_memory" not in tools
    assert "read_memory" in tools and "manage_triggers" in tools
    assert agent.mcp_manager.config.servers == []
    assert "system://index/<domain>" in agent.system_prompt

    created = await tools.execute_result("create_memory", {
        "parent_uri": "core://",
        "content": "归航时看见了灯塔。",
        "disclosure": "寻找锚地时阅读",
        "title": "锚地",
    })
    assert not created.is_error, created.text
    assert "core://锚地" in created.text

    workspace = agent.memory_workspace
    row, = workspace.directory("core")["entries"]
    document = workspace.document(row["document_id"])
    assert "归航时看见了灯塔。" in document["content"]
    workspace.save_document(document["id"], "灯塔已经熄灭。", document["revision"])
    assert "灯塔已经熄灭。" in await tools.execute("read_memory", {"uri": "core://锚地"})
