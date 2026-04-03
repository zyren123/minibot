from src.minibot.config.schema import MemoryConfig
from src.minibot.memory.manager import MemoryManager


def _make_manager(tmp_path):
    config = MemoryConfig(
        enabled=True,
        memory_dir=str(tmp_path / "memory"),
        backend="sqlite",
        active_namespace="ember-falls",
    )
    return MemoryManager(config, tmp_path / ".minibot", tmp_path)


def _seed_story(manager: MemoryManager) -> None:
    manager.create_namespace("ember-falls", "Ember Falls")
    manager.create_memory(parent_uri=None, slug="characters", title="Characters", kind="folder")
    manager.create_memory(parent_uri=None, slug="locations", title="Locations", kind="folder")
    manager.create_memory(
        parent_uri="memory://locations",
        slug="bailu-town",
        title="白露镇",
        kind="memory",
        node_type="location",
        content="河边小镇。",
    )
    manager.create_memory(
        parent_uri="memory://characters",
        slug="ali",
        title="Ali",
        kind="memory",
        node_type="character",
        content="她小时候生活在白露镇。",
        is_core=True,
        priority=10,
    )


def test_system_boot_returns_namespace_core_root_and_recent_nodes(tmp_path):
    manager = _make_manager(tmp_path)
    _seed_story(manager)

    manager.read("memory://characters/ali")
    payload = manager.read("system://boot")

    assert "Namespace:" in payload
    assert "Core memories" in payload
    assert "Recently active" in payload
    assert "memory://characters/ali" in payload


def test_manage_triggers_enforces_namespace_unique_terms(tmp_path):
    manager = _make_manager(tmp_path)
    _seed_story(manager)
    manager.create_memory(
        parent_uri="memory://locations",
        slug="other-town",
        title="别的镇",
        kind="memory",
        node_type="location",
        content="另一个地点。",
    )

    manager.manage_triggers("memory://locations/bailu-town", add=["白露镇"])

    try:
        manager.manage_triggers("memory://locations/other-town", add=["白露镇"])
    except ValueError as exc:
        assert "trigger" in str(exc).lower()
    else:
        raise AssertionError("expected duplicate trigger to fail")


def test_read_memory_renders_glossary_links_inline(tmp_path):
    manager = _make_manager(tmp_path)
    _seed_story(manager)
    manager.manage_triggers("memory://locations/bailu-town", add=["白露镇"])

    body = manager.read("memory://characters/ali")

    assert "[白露镇](memory://locations/bailu-town)" in body
