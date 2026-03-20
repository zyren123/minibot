from pathlib import Path

import pytest

from src.minibot.config.settings import load_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_config_from_global_only(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    workdir = project_root / "src"
    workdir.mkdir(parents=True)

    _write(
        app_home / "config" / "default.yaml",
        """
skills_dir: custom-skills
llm:
  model: custom-model
tools:
  timeout: 30
  disabled: ["Task"]
""".strip(),
    )
    _write(
        app_home / "config" / "hooks.yaml",
        """
hooks_dir: custom-hooks
enabled: false
hooks: []
""".strip(),
    )
    _write(app_home / "config" / "mcp_servers.yaml", "enabled: false\nservers: []\n")

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_ID", raising=False)

    config = load_config(workdir=workdir, app_home=app_home, project_root=project_root)

    assert config.workdir == workdir.resolve()
    assert config.project_root == project_root.resolve()
    assert config.app_home == app_home.resolve()
    assert config.llm.model == "custom-model"
    assert config.tools.timeout == 30
    assert config.tools.disabled == ["Task"]
    assert config.skills_dir == (app_home / "config" / "custom-skills").resolve()
    assert config.hooks.enabled is False
    assert config.hooks.hooks_dir == (app_home / "config" / "custom-hooks").resolve()
    assert config.mcp.enabled is False
    assert config.memory.memory_dir == str((app_home / "memory").resolve())


def test_load_config_env_precedence_process_over_global(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(
        app_home / ".env",
        "OPENAI_API_KEY=global-key\nMODEL_ID=global-model\nOPENAI_BASE_URL=https://global.test/v1\n",
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
    assert config.llm.model == "global-model"
    assert config.llm.base_url == "https://global.test/v1"


def test_yaml_env_placeholder_reads_from_dotenv_defaults(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(app_home / ".env", "MODEL_ID=from-global-dotenv\n")
    _write(
        app_home / "config" / "default.yaml",
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

    assert config.llm.model == "from-global-dotenv"


def test_bootstrap_global_config_writes_templates(tmp_path):
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
    assert config.llm.stream_enabled is True
    assert config.teams.debug_teammate_output is False
    assert config.skills_dir == (app_home / "skills").resolve()
    assert config.memory.memory_dir == str((app_home / "memory").resolve())


def test_load_config_continues_when_global_bootstrap_is_not_writable(tmp_path, monkeypatch):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    global_config_dir = (app_home / "config").resolve()
    original_mkdir = Path.mkdir

    def _readonly_mkdir(self, *args, **kwargs):
        if self.resolve() == global_config_dir:
            raise PermissionError("read-only app home")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _readonly_mkdir)

    # Note: Because app_home/config is read-only and missing, _load_yaml will return {}
    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert config.llm.model == "gpt-4.1-mini"
    assert config.hooks.enabled is True
    assert config.mcp.enabled is True


@pytest.mark.parametrize(
    ("target_file", "content", "missing_env", "expected_key"),
    [
        ("default.yaml", "skills_dir: ${SKILLS_DIR}\n", "SKILLS_DIR", "skills_dir"),
        ("hooks.yaml", "hooks_dir: ${HOOKS_DIR}\nenabled: true\nhooks: []\n", "HOOKS_DIR", "hooks_dir"),
        ("default.yaml", "memory:\n  memory_dir: ${MEMORY_DIR}\n", "MEMORY_DIR", "memory.memory_dir"),
    ],
)
def test_load_config_rejects_unset_env_path_placeholders(
    tmp_path,
    monkeypatch,
    target_file,
    content,
    missing_env,
    expected_key,
):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(app_home / "config" / target_file, content)

    monkeypatch.delenv(missing_env, raising=False)

    with pytest.raises(ValueError, match=expected_key):
        load_config(
            workdir=project_root,
            app_home=app_home,
            project_root=project_root,
        )


def test_load_config_parses_stream_and_teammate_debug_flags(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    _write(
        app_home / "config" / "default.yaml",
        """
llm:
  stream_enabled: false
teams:
  debug_teammate_output: true
""".strip(),
    )

    config = load_config(
        workdir=project_root,
        app_home=app_home,
        project_root=project_root,
    )

    assert config.llm.stream_enabled is False
    assert config.teams.debug_teammate_output is True
