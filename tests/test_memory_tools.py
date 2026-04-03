import pytest
from src.minibot.agent import Agent
from src.minibot.config.schema import (
    Config,
    HooksConfig,
    LLMConfig,
    MCPConfig,
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


def test_memory_tools_register_crud_search_and_trigger_management(tmp_path):
    cfg = Config(
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
        memory=MemoryConfig(enabled=True, memory_dir=str(tmp_path / "memory"), backend="sqlite"),
        teams=TeamsConfig(enabled=False),
        session=SessionConfig(enabled=False),
    )

    agent = Agent(config=cfg)

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
