from pathlib import Path


def test_memoryview_defaults_to_tree_and_guards_one_time_init() -> None:
    source = Path("webui/src/views/MemoryView.tsx").read_text(encoding="utf-8")

    assert 'const [viewMode, setViewMode] = useState<ViewMode>("tree");' in source
    assert "const hasInitializedRef = useRef(false);" in source
    assert "if (hasInitializedRef.current) return;" in source


def test_memoryview_does_not_eagerly_fetch_first_node_detail_on_namespace_load() -> None:
    source = Path("webui/src/views/MemoryView.tsx").read_text(encoding="utf-8")

    assert 'const initialUri = firstNodeUri(treePayload.nodes);' not in source
    assert 'const nodePayload = await getMemoryNode(botId, initialUri);' not in source


def test_memoryview_uses_manual_node_mutation_apis() -> None:
    view_source = Path("webui/src/views/MemoryView.tsx").read_text(encoding="utf-8")
    api_source = Path("webui/src/lib/api.ts").read_text(encoding="utf-8")

    assert "updateMemoryNode" in api_source
    assert "deleteMemoryNode" in api_source
    assert "deleteMemoryNodes" in api_source
    assert "updateMemoryNode(" in view_source
    assert "deleteMemoryNode(" in view_source
    assert "deleteMemoryNodes(" in view_source
