from src.minibot.config import load_config


def test_memory_config_parses_backend_database_url_and_active_namespace(tmp_path):
    app_home = tmp_path / "app-home"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    config = load_config(workdir=project_root, app_home=app_home, project_root=project_root)

    assert config.memory.backend == "sqlite"
    assert config.memory.database_url is None
    assert config.memory.active_namespace is None

