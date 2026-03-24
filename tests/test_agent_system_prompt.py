from pathlib import Path
from datetime import datetime

from src.minibot.agent import Agent
from src.minibot.config.schema import (
    Config,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    MemoryConfig,
    TeamsConfig,
)


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
    assert "## Memory (Auto-loaded)" in prompt
    assert "Prefer concise updates" in prompt
    assert "Continue prompt overhaul" in prompt
    assert "## Team Orchestration Policy" in prompt
    assert prompt.rstrip().endswith(extra)


def test_teammate_prompt_uses_teammate_constraints(tmp_path):
    agent = _make_agent(tmp_path, role="teammate")

    prompt = agent.system_prompt

    assert "## Team Role Constraints" in prompt
    assert "You are teammate `member-1` in team `team-1`." in prompt
    assert "You MUST NOT create teammates or delegate with `Task`." in prompt
    assert "## Team Orchestration Policy" not in prompt
