import importlib.util
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(script_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(script_path.parent))
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_list_skills_script_marks_installed_entries(tmp_path: Path, capsys) -> None:
    script = REPO_ROOT / "src/minibot/builtin_skills/skill-installer/scripts/list-skills.py"
    module = _load_script(script, "test_list_skills_script")

    installed_dir = tmp_path / "skills"
    (installed_dir / "beta").mkdir(parents=True)

    payload = json.dumps(
        [
            {"name": "beta", "type": "dir"},
            {"name": "alpha", "type": "dir"},
            {"name": "README.md", "type": "file"},
        ]
    ).encode("utf-8")

    module._request = lambda url: payload
    exit_code = module.main(["--format", "json", "--installed-dir", str(installed_dir)])
    out = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert json.loads(out) == [
        {"name": "alpha", "installed": False},
        {"name": "beta", "installed": True},
    ]


def test_install_skill_from_github_script_copies_skill_tree(tmp_path: Path, capsys) -> None:
    script = REPO_ROOT / "src/minibot/builtin_skills/skill-installer/scripts/install-skill-from-github.py"
    module = _load_script(script, "test_install_skill_script")

    def fake_prepare_repo(source, method, tmp_dir):
        repo_root = Path(tmp_dir) / "repo-root"
        skill_dir = repo_root / "skills" / "hello-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: hello-skill\ndescription: Hello skill\n---\n\nBody.\n",
            encoding="utf-8",
        )
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "helper.py").write_text("print('ok')\n", encoding="utf-8")
        return str(repo_root)

    module._prepare_repo = fake_prepare_repo
    dest = tmp_path / "installed-skills"
    exit_code = module.main(
        [
            "--repo",
            "owner/repo",
            "--path",
            "skills/hello-skill",
            "--dest",
            str(dest),
        ]
    )
    out = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert "Installed hello-skill" in out
    assert (dest / "hello-skill" / "SKILL.md").exists()
    assert (dest / "hello-skill" / "scripts" / "helper.py").exists()


def test_init_skill_script_creates_layout_and_examples(tmp_path: Path) -> None:
    script = REPO_ROOT / "src/minibot/builtin_skills/skill-creator/scripts/init_skill.py"
    module = _load_script(script, "test_init_skill_script")

    skill_dir = module.init_skill(
        "demo-skill",
        tmp_path,
        ["scripts", "references", "assets"],
        include_examples=True,
    )

    assert skill_dir == tmp_path / "demo-skill"
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "scripts" / "example.py").exists()
    assert (skill_dir / "references" / "reference.md").exists()
    assert (skill_dir / "assets" / "example_asset.txt").exists()


def test_init_skill_script_defaults_to_user_skills_dir(tmp_path: Path, monkeypatch) -> None:
    script = REPO_ROOT / "src/minibot/builtin_skills/skill-creator/scripts/init_skill.py"
    module = _load_script(script, "test_init_skill_default_dir_script")

    app_home = tmp_path / "app-home"
    app_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINIBOT_HOME", str(app_home))

    assert module.resolve_default_output_dir() == (app_home / "skills").resolve()


def test_quick_validate_script_validates_skill_structure(tmp_path: Path) -> None:
    script = REPO_ROOT / "src/minibot/builtin_skills/skill-creator/scripts/quick_validate.py"
    module = _load_script(script, "test_quick_validate_script")

    valid_skill = tmp_path / "valid-skill"
    valid_skill.mkdir()
    (valid_skill / "SKILL.md").write_text(
        "---\nname: valid-skill\ndescription: A valid skill.\n---\n\n# Valid\n\nBody.\n",
        encoding="utf-8",
    )

    invalid_skill = tmp_path / "invalid-skill"
    invalid_skill.mkdir()
    (invalid_skill / "SKILL.md").write_text(
        "---\nname: invalid-skill\n---\n\nBody.\n",
        encoding="utf-8",
    )

    assert module.validate_skill(valid_skill) == (True, "Skill is valid")
    ok, message = module.validate_skill(invalid_skill)
    assert ok is False
    assert "description" in message.lower()
