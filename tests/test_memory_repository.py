from src.minibot.config.schema import MemoryConfig
from src.minibot.memory.repository import MemoryRepository


def test_sqlite_repository_bootstraps_schema_and_default_db_path(tmp_path):
    config = MemoryConfig(memory_dir=str(tmp_path / "memory"), backend="sqlite")

    repo = MemoryRepository.from_config(config, app_home=tmp_path / ".minibot")

    assert repo.database_path is not None
    assert repo.database_path.name == "memory_v2.sqlite3"
    assert repo.database_path.exists()


def test_repository_factory_uses_sqlmodel_memory_database(tmp_path):
    config = MemoryConfig(memory_dir=str(tmp_path / "memory"), backend="sqlite")

    repo = MemoryRepository.from_config(config, app_home=tmp_path / ".minibot")

    assert repo.database.engine is not None
    assert repo.database.session_factory is not None


def test_create_update_move_and_delete_node_records_versions(tmp_path):
    config = MemoryConfig(memory_dir=str(tmp_path / "memory"), backend="sqlite")
    repo = MemoryRepository.from_config(config, app_home=tmp_path / ".minibot")

    namespace = repo.create_namespace(slug="ember-falls", title="Ember Falls")
    repo.create_node(
        namespace.slug,
        parent_uri=None,
        slug="characters",
        title="Characters",
        kind="folder",
    )
    ali = repo.create_node(
        namespace.slug,
        parent_uri="memory://characters",
        slug="ali",
        title="Ali",
        kind="memory",
        node_type="character",
        content="POV protagonist.",
    )

    updated = repo.update_node(namespace.slug, "memory://characters/ali", title="Ali Ren")
    assert updated.title == "Ali Ren"
    assert updated.uri == "memory://characters/ali"

    moved = repo.update_node(
        namespace.slug,
        "memory://characters/ali",
        parent_uri=None,
        slug="protagonist",
    )
    assert moved.uri == "memory://protagonist"

    with_repo = repo.get_node_by_uri(namespace.slug, "memory://characters")
    assert with_repo is not None

    versions = repo.list_versions(ali.id)
    assert [version["operation"] for version in versions] == ["create", "update", "update"]


def test_delete_node_removes_entire_subtree(tmp_path):
    config = MemoryConfig(memory_dir=str(tmp_path / "memory"), backend="sqlite")
    repo = MemoryRepository.from_config(config, app_home=tmp_path / ".minibot")

    namespace = repo.create_namespace(slug="ember-falls", title="Ember Falls")
    root = repo.create_node(
        namespace.slug,
        parent_uri=None,
        slug="characters",
        title="Characters",
        kind="folder",
    )
    child = repo.create_node(
        namespace.slug,
        parent_uri=root.uri,
        slug="ali",
        title="Ali",
        kind="memory",
        content="POV protagonist.",
    )
    grandchild = repo.create_node(
        namespace.slug,
        parent_uri=child.uri,
        slug="notes",
        title="Notes",
        kind="memory",
        content="Nested note.",
    )

    repo.delete_node(namespace.slug, root.uri)

    assert repo.get_node_by_uri(namespace.slug, root.uri) is None
    assert repo.get_node_by_uri(namespace.slug, child.uri) is None
    assert repo.get_node_by_uri(namespace.slug, grandchild.uri) is None
    assert repo.list_nodes(namespace.slug) == []
