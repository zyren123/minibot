from pathlib import Path

from src.minibot.config.settings import load_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_config_merges_global_and_project_with_source_aware_paths(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    workdir = project_root / "src"
    workdir.mkdir(parents=True)

    _write(
        app_home / "config" / "default.yaml",
        """
skills_dir: global-skills
llm:
  model: global-model
tools:
  timeout: 30
""".strip(),
    )
    _write(
        project_root / "config" / "default.yaml",
        """
skills_dir: project-skills
llm:
  model: project-model
tools:
  disabled: ["Task"]
""".strip(),
    )
    _write(
        app_home / "config" / "hooks.yaml",
        """
hooks_dir: global-hooks
enabled: true
hooks: []
""".strip(),
    )
    _write(
        project_root / "config" / "hooks.yaml",
        """
enabled: false
hooks: []
""".strip(),
    )
    _write(app_home / "config" / "mcp_servers.yaml", "enabled: true\nservers: []\n")
    _write(project_root / "config" / "mcp_servers.yaml", "enabled: false\nservers: []\n")

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_ID", raising=False)

    config = load_config(workdir=workdir, app_home=app_home, project_root=project_root)

    assert config.workdir == workdir.resolve()
    assert config.project_root == project_root.resolve()
    assert config.app_home == app_home.resolve()
    assert config.llm.model == "project-model"
    assert config.tools.timeout == 30
    assert config.tools.disabled == ["Task"]
    assert config.skills_dir == (project_root / "config" / "project-skills").resolve()
    assert config.hooks.enabled is False
    assert config.hooks.hooks_dir == (app_home / "config" / "global-hooks").resolve()
    assert config.mcp.enabled is False
    assert config.memory.memory_dir == str((app_home / "memory").resolve())


def test_load_config_env_precedence_process_over_project_over_global(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(app_home / ".env", "OPENAI_API_KEY=global-key\nMODEL_ID=global-model\n")
    _write(
        project_root / ".env",
        "OPENAI_API_KEY=project-key\nMODEL_ID=project-model\nOPENAI_BASE_URL=https://example.test/v1\n",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "process-key")
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert config.llm.api_key == "process-key"
    assert config.llm.model == "project-model"
    assert config.llm.base_url == "https://example.test/v1"


def test_yaml_env_placeholder_reads_from_dotenv_defaults(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(project_root / ".env", "MODEL_ID=from-project-dotenv\n")
    _write(
        project_root / "config" / "default.yaml",
        """
llm:
  model: ${MODEL_ID:gpt-4.1-mini}
""".strip(),
    )
    monkeypatch.delenv("MODEL_ID", raising=False)

    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert config.llm.model == "from-project-dotenv"


def test_bootstrap_global_config_copies_from_project_when_missing(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(
        project_root / "config" / "default.yaml",
        """
llm:
  model: project-seed-model
""".strip(),
    )
    _write(
        project_root / "config" / "hooks.yaml",
        """
enabled: false
hooks: []
""".strip(),
    )
    _write(project_root / "config" / "mcp_servers.yaml", "enabled: false\nservers: []\n")

    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert (app_home / "config" / "default.yaml").exists()
    assert (app_home / "config" / "hooks.yaml").exists()
    assert (app_home / "config" / "mcp_servers.yaml").exists()
    assert config.llm.model == "project-seed-model"
    assert config.hooks.enabled is False
    assert config.mcp.enabled is False


def test_bootstrap_global_config_writes_templates_when_project_config_absent(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert (app_home / "config" / "default.yaml").exists()
    assert (app_home / "config" / "hooks.yaml").exists()
    assert (app_home / "config" / "mcp_servers.yaml").exists()
    assert config.tools.timeout == 60
    assert config.memory.memory_dir == str((app_home / "memory").resolve())
