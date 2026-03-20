---
name: skill-installer
description: Install, update, remove, or inspect Minibot skills. Use when the user wants to add a skill from a local folder or GitHub repo, or asks where skills should be placed.
---

# Skill Installer

Use this skill when the user wants to install or manage Minibot skills.

## What a skill looks like

A skill is a directory containing:

- `SKILL.md` with `name` and `description` frontmatter
- optional `scripts/`
- optional `references/`
- optional `assets/`

## Installation workflow

1. Inspect the active skills directories before making changes.
2. Default installs to the user skills directory so new skills do not land in the repo by accident.
3. Only install into a project `skills/` directory when the user explicitly asks for a repo-scoped shared skill.
4. Install by copying a full skill folder into that directory.
5. Do not overwrite an existing skill directory unless the user explicitly asked to replace it.
6. After install, verify the skill is visible from the current runtime.

## Helper scripts

Use the bundled scripts when the task matches:

- `scripts/list-skills.py`
  - Lists available skills from a GitHub repo path.
  - Default source is `openai/skills` `skills/.curated`.
  - Marks skills already installed in the current Minibot skills directory.
- `scripts/install-skill-from-github.py`
  - Installs one or more skill folders from GitHub into the active Minibot skills directory.
  - Supports direct download for public repos and git sparse checkout fallback.
- `scripts/github_utils.py`
  - Shared GitHub and Minibot path resolution helpers for the installer scripts.

These scripts may need network access. If the environment is sandboxed, request escalation before running them.

## Directory and precedence rules

- Higher-priority skill directories override lower-priority ones when names collide.
- Builtin skills are only defaults. User or project skills may override them with the same name.
- If the user wants a skill gone from the current bot, disabling it is safer than deleting files.

## Common tasks

### Install from a local folder

- Verify the source contains a valid `SKILL.md`.
- Copy the whole folder into the target skills directory.
- Keep the folder name aligned with the skill name unless there is a reason not to.

### Install from GitHub

- Use `scripts/install-skill-from-github.py` when possible.
- Prefer fetching only the required skill folder, not the entire repository, when practical.
- If credentials or network access are required, say so clearly.
- Preserve bundled `scripts/`, `references/`, and `assets/`.

### Update an installed skill

- Compare the existing target directory with the source first.
- Replace only when the user asked for an update or confirmed the change.
- Call out when a local customized version would be overwritten.

### Remove a skill

- Prefer disabling first.
- Delete the skill directory only when the user explicitly asks to remove it from disk.

## Verification

Use one of these checks after installation or removal:

- `/skills list` in the TUI
- Web API `/api/skills`
- `SkillLoader(...).list_skills()` in Python
- `scripts/list-skills.py --format json`

Report the active directory used and whether the skill overrides a builtin or lower-priority copy.
