---
name: skill-creator
description: Design or update Minibot skills. Use when the user wants a new skill, wants to improve an existing skill, or needs help structuring SKILL.md, scripts, references, or assets.
---

# Skill Creator

Use this skill when creating or revising a Minibot skill.

## Goal

Create a compact skill that gives the agent workflow knowledge it would not reliably infer on its own.

## Helper scripts

- `scripts/init_skill.py`
  - Creates a new skill directory with a starter `SKILL.md`.
  - Defaults to the user's Minibot skills directory, not the current repo.
  - Can optionally create `scripts/`, `references/`, and `assets/`.
  - Can also drop example files into those directories for faster bootstrapping.
- `scripts/quick_validate.py`
  - Performs a lightweight structural validation of a skill directory.
  - Use it before declaring a new or edited skill complete.

## Skill anatomy

Each skill is a folder:

```text
skill-name/
├── SKILL.md
├── scripts/      # optional deterministic helpers
├── references/   # optional docs loaded only when needed
└── assets/       # optional templates or output resources
```

`SKILL.md` must include frontmatter with:

- `name`
- `description`

## Writing rules

- Keep `description` concrete so the loader can match the skill to user intent.
- Keep the body short and procedural.
- Assume the model is already generally capable; only include task-specific guidance.
- Put long reference material in `references/`, not in `SKILL.md`.
- Use scripts when deterministic behavior matters or the same code would otherwise be rewritten repeatedly.

## Recommended creation workflow

1. Inspect nearby skills first so naming and structure stay consistent.
2. Default new skills to the user skills directory. Only write to a project `skills/` folder when the user explicitly wants a repo-scoped shared skill.
3. Define the user tasks that should trigger the skill.
4. Write a precise `description` that names those tasks clearly.
5. Put only the core workflow in `SKILL.md`.
6. Add `references/` or `scripts/` only when they materially reduce ambiguity or repetition.
7. Verify the skill appears in `/skills list` or `/api/skills`.
8. Run `scripts/quick_validate.py <skill_dir>`.
9. Test the skill on one realistic prompt and tighten unclear instructions.

## Good defaults

- Prefer one skill per focused workflow.
- Prefer direct instructions over long theory.
- Prefer explicit verification steps.
- Prefer stable file layouts and predictable helper script names.

## Updating an existing skill

- Preserve the skill name unless the trigger intent changes.
- Remove stale guidance instead of appending contradictory notes.
- If the skill supports multiple variants, keep selection guidance in `SKILL.md` and move variant details into separate reference files.

## Validation checklist

- The frontmatter is valid.
- The description is specific enough to trigger at the right time.
- The body stays concise.
- Any referenced files actually exist.
- The workflow can be followed with Minibot's current tools and environment.
