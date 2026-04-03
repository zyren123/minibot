"""Render helpers for memory output."""

from __future__ import annotations


def render_markdown_links(content: str, glossary: list[dict[str, str]]) -> str:
    """Render glossary terms as inline Markdown links."""
    rendered = content
    replacements: list[tuple[str, str]] = []
    for index, entry in enumerate(sorted(glossary, key=lambda item: len(item["term"]), reverse=True)):
        term = entry["term"]
        uri = entry["uri"]
        if not term or term not in rendered:
            continue
        placeholder = f"@@MEM_LINK_{index}@@"
        rendered = rendered.replace(term, placeholder)
        replacements.append((placeholder, f"[{term}]({uri})"))
    for placeholder, value in replacements:
        rendered = rendered.replace(placeholder, value)
    return rendered
