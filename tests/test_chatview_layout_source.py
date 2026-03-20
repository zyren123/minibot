from pathlib import Path


def test_chatview_does_not_render_duplicate_desktop_sidebar_toggle() -> None:
    source = Path("webui/src/views/ChatView.tsx").read_text(encoding="utf-8")

    assert 'setSidebarCollapsed((prev) => !prev)' not in source
