import os
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from src.minibot.config.schema import MemoryConfig
from src.minibot.memory.workspace import (
    FileMemoryWorkspace,
    MemoryRevisionConflict,
    open_novelmemo_workspace,
)


def _file_workspace(tmp_path: Path) -> FileMemoryWorkspace:
    return FileMemoryWorkspace(MemoryConfig(), tmp_path / "home", tmp_path / "project")


def test_file_workspace_lists_original_markdown_and_coordinates_edits(tmp_path):
    workspace = _file_workspace(tmp_path)
    counterpart = _file_workspace(tmp_path)
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_id = f"daily/{today}.md"
    yesterday_id = f"daily/{yesterday}.md"

    assert [entry["id"] for entry in workspace.directory("")["entries"]] == ["LONG_TERM.md", "daily"]
    assert [entry["id"] for entry in workspace.directory("daily")["entries"]] == [today_id]
    assert not (workspace.root / "memory_v2.sqlite3").exists()

    long_term = workspace.document("LONG_TERM.md")
    counterpart.edit_file("LONG_TERM.md", operation="overwrite", text="运行约定\n")
    with pytest.raises(MemoryRevisionConflict):
        workspace.save_document("LONG_TERM.md", "过期的编辑", long_term["revision"])

    today_document = workspace.document(today_id)
    workspace.save_document(today_id, "每日中文笔记", today_document["revision"])
    counterpart.edit_file(yesterday_id, operation="append", text="昨天的记录")
    assert (workspace.root / today_id).read_text(encoding="utf-8") == "每日中文笔记"
    assert [entry["id"] for entry in workspace.directory("daily")["entries"]] == [today_id, yesterday_id]
    assert workspace.search("中文笔记")[0]["id"] == today_id
    assert "每日中文笔记" in workspace.get_context_for_prompt()


def test_file_workspace_checks_dates_and_missing_document_revisions(tmp_path):
    workspace = _file_workspace(tmp_path)
    with pytest.raises(ValueError):
        workspace.document("daily/2026-02-31.md")
    with pytest.raises(ValueError):
        workspace.document("../LONG_TERM.md")
    missing = workspace.document("LONG_TERM.md")
    created = workspace.save_document("LONG_TERM.md", "", missing["revision"])
    assert created["revision"] != missing["revision"]


def _vault(root: Path) -> Path:
    pytest.importorskip("novelmemo")
    config = root / ".memory" / "memory.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("valid_domains: [writer]\nboot_uris: {}\n", encoding="utf-8")
    folder = root / "writer" / "人物"
    folder.mkdir(parents=True)
    (folder / "_index.md").write_text(
        '---\ndisclosure: "人物资料: 进场时查看"\n---\n\n人物目录\n', encoding="utf-8"
    )
    (folder / "宁宁.md").write_text(
        '---\ndisclosure: "人物: 宁宁"\ntriggers: [沉舟线]\nx_sample: "不应修改: 备注"\n---\n\n人物的旧笔记\n',
        encoding="utf-8",
    )
    return root


def test_novelmemo_workspace_shares_real_files_triggers_and_revision(tmp_path):
    root = _vault(tmp_path / "写作")
    workspace = open_novelmemo_workspace(root)
    counterpart = open_novelmemo_workspace(str(root))
    assert workspace is counterpart

    folder_id = "writer/人物"
    folder_note = f"{folder_id}/_index.md"
    note = f"{folder_id}/宁宁.md"
    assert workspace.directory("")["entries"][0]["id"] == "writer"
    assert workspace.directory("writer")["entries"][0]["document_id"] == folder_note
    listing = workspace.directory(folder_id)
    assert [part["id"] for part in listing["breadcrumbs"]] == ["", "writer", folder_id]
    assert [entry["id"] for entry in listing["entries"]] == [note]
    assert {entry["id"] for entry in workspace.graph()} == {"writer", folder_id, note}

    original = workspace.document(note)
    assert original["triggers"] == ["沉舟线"]
    assert original["disclosure"] == "人物: 宁宁"
    edited = workspace.save_document(
        note, "人物对白。", original["revision"],
        triggers=["新线索: 台词"], disclosure="写作: 出场 --- 的时机",
    )
    assert edited["triggers"] == ["新线索: 台词"]
    assert edited["disclosure"] == "写作: 出场 --- 的时机"
    assert workspace.search("新线索")[0]["id"] == note
    assert "人物对白" in counterpart.read_memory("writer://人物/宁宁")
    metadata = yaml.safe_load((root / note).read_text(encoding="utf-8").split("---", 2)[1])
    assert metadata["x_sample"] == "不应修改: 备注"

    counterpart.manage_triggers("writer://人物/宁宁", add=["续集"])
    with pytest.raises(MemoryRevisionConflict):
        workspace.save_document(note, "过期编辑", edited["revision"])
    assert workspace.document(note)["triggers"] == ["新线索: 台词", "续集"]


def test_novelmemo_rejects_a_revision_from_another_vault(tmp_path):
    first = open_novelmemo_workspace(_vault(tmp_path / "first"))
    second = open_novelmemo_workspace(_vault(tmp_path / "second"))
    note = "writer/人物/宁宁.md"
    original = first.document(note)
    assert second.document(note)["content"] == original["content"]
    with pytest.raises(MemoryRevisionConflict):
        second.save_document(note, "外部编辑", original["revision"])


def test_novelmemo_grounds_agent_mutations_in_workspace(tmp_path):
    workspace = open_novelmemo_workspace(_vault(tmp_path / "novel"))
    uri = workspace.create_memory("writer://人物", "新增的正文。", "新增: 阅读线索 ---", "松树")
    path = "writer/人物/松树.md"
    assert workspace.document(path)["disclosure"] == "新增: 阅读线索 ---"
    with pytest.raises(ValueError):
        workspace.create_memory("writer://人物", "不能覆盖", "加入", "松树")
    workspace.update_memory(uri, old_string="新增", new_string="更新")
    assert "更新的正文" in workspace.document(path)["content"]
    workspace.delete_memory(uri)
    assert all(entry["id"] != path for entry in workspace.directory("writer/人物")["entries"])


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks can require local privileges on Windows")
def test_novelmemo_refuses_symlinks_and_agent_path_escape(tmp_path):
    root = _vault(tmp_path / "novel")
    unrelated = tmp_path / "outside.md"
    unrelated.write_text("private", encoding="utf-8")
    link = root / "writer" / "人物" / "link.md"
    link.symlink_to(unrelated)
    with pytest.raises(ValueError, match="Symbolic links"):
        open_novelmemo_workspace(root)

    link.unlink()
    workspace = open_novelmemo_workspace(root)
    with pytest.raises(ValueError):
        workspace.read_memory("writer://../../outside")
    with pytest.raises(ValueError):
        workspace.document("../outside.md")
