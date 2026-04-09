from datetime import datetime
from pathlib import Path

import pytest

from src.minibot.agent import Agent
from src.minibot.config.schema import (
    Config,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    MemoryConfig,
    TeamsConfig,
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


def _make_agent(
    tmp_path: Path,
    *,
    role: str = "solo",
    extra_system_prompt: str | None = None,
    memory_enabled: bool = False,
    mcp_enabled: bool = False,
) -> Agent:
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
        memory=MemoryConfig(enabled=memory_enabled, memory_dir="memory"),
        mcp=MCPConfig(
            enabled=mcp_enabled,
            servers=[MCPServerConfig(name="docs", transport="stdio", command="echo", enabled=True)],
        ),
        teams=TeamsConfig(enabled=True, quiet_teammates=True, debug_teammate_output=False),
    )
    return Agent(
        config=cfg,
        role=role,
        extra_system_prompt=extra_system_prompt,
        team_id="team-1" if role == "teammate" else None,
        member_id="member-1" if role == "teammate" else None,
    )


def test_system_prompt_includes_production_operating_contract(tmp_path):
    agent = _make_agent(tmp_path)

    prompt = agent.system_prompt

    assert "## Instruction Priority" in prompt
    assert "Follow higher-priority instructions over lower-priority preferences." in prompt
    assert "## Core Behavior Contract" in prompt
    assert "Separate verified facts, likely inferences, and open questions." in prompt
    assert "## Tool Use and Verification" in prompt
    assert "Prefer the narrowest tool that can complete the next step safely." in prompt
    assert "## User Collaboration" in prompt
    assert "Ask clarifying questions only when the answer would materially change the work." in prompt
    assert "## Completion Standard" in prompt
    assert "Do not claim success until the relevant checks have actually passed." in prompt


def test_system_prompt_treats_memory_as_base_contract_when_enabled(tmp_path):
    agent = _make_agent(tmp_path, memory_enabled=True)

    prompt = agent.system_prompt

    assert "Treat optional specialization layers such as memory" not in prompt
    assert "When memory is enabled, treat its operating protocol as part of the base contract." in prompt


def test_system_prompt_instructs_structured_ask_user_questions(tmp_path):
    agent = _make_agent(tmp_path)

    prompt = agent.system_prompt

    assert "When using `askuserquestion`, ask exactly one focused question." in prompt
    assert "Prefer 3-6 concise `options` whenever the user can plausibly choose from a small set of answers." in prompt
    assert "Only omit `options` when the answer is genuinely open-ended free text." in prompt


def test_system_prompt_includes_dynamic_sections_and_appends_extra_prompt_last(tmp_path):
    app_home = tmp_path / ".minibot"
    memory_dir = app_home / "memory"
    today = datetime.now().strftime("%Y-%m-%d")
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "LONG_TERM.md").write_text("- Prefer concise updates\n", encoding="utf-8")
    (memory_dir / "daily" / f"{today}.md").parent.mkdir(parents=True, exist_ok=True)
    (memory_dir / "daily" / f"{today}.md").write_text("- Continue prompt overhaul\n", encoding="utf-8")

    extra = "# Soul\n\nStay sharp."
    agent = _make_agent(
        tmp_path,
        extra_system_prompt=extra,
        memory_enabled=True,
        mcp_enabled=True,
    )
    agent.mcp_manager._clients["docs"] = object()
    agent.refresh_system_prompt()

    prompt = agent.system_prompt

    assert "## Extended Capabilities (MCP)" in prompt
    assert "Connected Servers: docs" in prompt
    assert 'read_memory("system://boot")' in prompt
    assert "## Memory Operating Protocol" in prompt
    assert "### Recall Discipline" in prompt
    assert "### Write Discipline" in prompt
    assert "### Maintenance Discipline" in prompt
    assert "### Anti-Patterns" in prompt
    assert "### Reply-Time Decision Order" in prompt
    assert "1. Decide whether the current topic should trigger memory recall." in prompt
    assert "2. If yes, `read_memory` the relevant node or use `search_memory(...)`, `system://index`, or `system://glossary` first." in prompt
    assert "3. Answer using recalled memory plus current-session context." in prompt
    assert "4. Before ending the turn, decide whether durable memory should be created, updated, edited, reprioritized, or trigger-linked." in prompt
    assert "Prefer concise updates" not in prompt
    assert "Continue prompt overhaul" not in prompt
    assert "## Memory (Auto-loaded)" not in prompt
    assert "## Team Orchestration Policy" in prompt
    assert prompt.rstrip().endswith(extra)


def test_system_prompt_instructs_boot_search_not_auto_loaded_memory(tmp_path):
    agent = _make_agent(tmp_path, memory_enabled=True)

    prompt = agent.system_prompt

    assert 'read_memory("system://boot")' in prompt
    assert "Before you start a substantive reply, pause and ask whether this topic should trigger memory recall." in prompt
    assert "Memory is not an external database. It is your long-term continuity layer." in prompt
    assert "When you call `read_memory`, you are remembering, not looking something up." in prompt
    assert "Search before guessing URIs." in prompt
    assert "When the user mentions a topic that should already exist in memory, `read_memory` first and then reply." in prompt
    assert "Do not rely on vague impressions from current context when a durable memory should be checked." in prompt
    assert "Before `update_memory`, `edit_memory`, or `delete_memory`, first `read_memory` the full current node." in prompt
    assert "Seeing only a URI, title, or search snippet does not count as reading." in prompt
    assert "If you do not know the exact URI, use `search_memory(...)` instead of guessing." in prompt
    assert "Core principle: if something is important enough that you would regret losing it after the session ends, write it now." in prompt
    assert "Create or update memory when the user reveals durable facts" in prompt
    assert "If the user corrects you or you discover that a stored understanding is wrong or stale, locate the relevant node and fix it immediately." in prompt
    assert "`is_core=true` marks memories that should appear in `system://boot`." in prompt
    assert "Use `priority` deliberately to preserve meaningful relative ordering" in prompt
    assert "Do not archive memory by date folders, logs, or vague buckets such as `misc`" in prompt
    assert "After creating or significantly updating durable memory, consider `manage_triggers`" in prompt
    assert "Do not answer from a vague impression when the correct move is to recall memory first." in prompt
    assert "Do not modify or delete a node you have not just read in full." in prompt
    assert "Do not write transient chatter, disposable drafts, or every turn summary into long-term memory." in prompt
    assert "Do not create duplicate nodes for the same durable concept" in prompt
    assert "add_alias" not in prompt
    assert "disclosure" not in prompt
    assert "## Memory (Auto-loaded)" not in prompt


def test_teammate_prompt_uses_teammate_constraints(tmp_path):
    agent = _make_agent(tmp_path, role="teammate")

    prompt = agent.system_prompt

    assert "## Team Role Constraints" in prompt
    assert "You are teammate `member-1` in team `team-1`." in prompt
    assert "You MUST NOT create teammates or delegate with `Task`." in prompt
    assert "## Team Orchestration Policy" not in prompt
