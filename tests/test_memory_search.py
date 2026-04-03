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


def test_search_prefers_trigger_then_title_then_content(tmp_path):
    manager = _make_manager(tmp_path)
    manager.create_namespace("ember-falls", "Ember Falls")
    manager.create_memory(parent_uri=None, slug="locations", title="Locations", kind="folder")
    manager.create_memory(parent_uri=None, slug="notes", title="Notes", kind="folder")
    manager.create_memory(
        parent_uri="memory://locations",
        slug="bailu-town",
        title="河边故乡",
        kind="memory",
        node_type="location",
        content="主角小时候居住在这里。",
    )
    manager.create_memory(
        parent_uri="memory://notes",
        slug="chapter-1",
        title="Chapter 1",
        kind="memory",
        node_type="event",
        content="白露镇在第一章被提及。",
    )
    manager.manage_triggers("memory://locations/bailu-town", add=["白露镇"])

    results = manager.search("白露镇")

    assert results[0].uri == "memory://locations/bailu-town"
    assert results[0].matched_by == "trigger"


def test_search_accepts_kind_filters_inside_node_types_argument(tmp_path):
    manager = _make_manager(tmp_path)
    manager.create_namespace("ember-falls", "Ember Falls")
    manager.create_memory(parent_uri=None, slug="characters", title="Characters", kind="folder")
    manager.create_memory(
        parent_uri="memory://characters",
        slug="ding-he-yue",
        title="丁荷月",
        kind="memory",
        node_type="character",
        content="人物：丁荷月。身份：中国女性。",
    )

    results = manager.search("丁荷月", node_types=["memory", "folder"], include_folders=True)

    assert [result.uri for result in results] == ["memory://characters/ding-he-yue"]
