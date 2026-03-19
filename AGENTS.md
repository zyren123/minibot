# AGENTS.md

This file is a **permanent memory / onboarding note for coding agents** working on this repo.

## Quick Facts (Don’t Re-Learn From Scratch)

- **PyPI distribution name**: `minibotclaw`
  - **Import package** stays: `minibot`
  - **CLI scripts**: `minibot`, `minibot-web`
- **Release automation**: pushing a version tag `vX.Y.Z` triggers **PyPI publish + GitHub Release** via `.github/workflows/publish-pypi.yml`.
  - Do **not** rename/move `.github/workflows/publish-pypi.yml` unless you also update the PyPI Trusted Publisher config.
- **WebUI build**: `webui/` (Vite) builds to `webui/dist/` and is copied into `src/minibot/server/static/` **before packaging**.
  - After syncing a new WebUI build into `src/minibot/server/static/`, **delete old hashed files** in `src/minibot/server/static/assets/` that are not referenced by the current `index.html`.
  - Use the explicit sync procedure below; do not rely on a single `cp -R webui/dist/. ...` step as the only sync mechanism.

## Architecture Map

- **Core Agent**: `src/minibot/agent.py`
  - Event-driven ReAct loop; emits structured stream events (`assistant_delta`, `tool_call`, etc.).
- **SDK surface**: `src/minibot/sdk/minibot.py`
  - Public entrypoint is `minibot.Minibot` (re-exported in `src/minibot/__init__.py`).
  - Supports passing `session_id`, custom `tools` (raw Python callables), `skills_dir`, and `extra_system_prompt`.
- **Tools**: `src/minibot/tools/`
  - Supports wrapping Python functions into JSON-schema tools.
- **Skills**: `src/minibot/skills/loader.py`
  - Loads skills/prompts from a directory; SDK/server can override `skills_dir`.
- **Sessions / persistence**: `src/minibot/session/` (portable file metadata; Linux lacks `st_birthtime` so fall back is used).
- **Server**: `src/minibot/server/app.py`
  - FastAPI + SSE streaming endpoints; serves static UI from `src/minibot/server/static/`.
- **WebUI**: `webui/`
  - React + Vite + Tailwind; provides Chat/Config tabs and talks to the FastAPI API.

## Chat Message Notes

- Chat/session messages are now **structured objects**, not just `{role, content}`:
  - Persistent session messages may include `message_id`, `parent_user_message_id`, assistant `reasoning`, and assistant `usage`.
  - The WebUI chat actions and token display depend on those fields; if you change message shape, update agent events, server response models, and `webui/src/lib/types.ts` together.
- Message action semantics in the current WebUI:
  - `Regenerate` only applies to the **latest assistant turn**.
  - `Delete` removes the matched assistant’s **full turn** (paired user + same-turn tool/assistant messages up to the next user message).
- Copying `webui/dist/` into `src/minibot/server/static/` does **not** automatically prune old hashed files in `src/minibot/server/static/assets/`.
  - Project rule: after each build sync, remove stale hashed files so `src/minibot/server/static/assets/` contains only the files referenced by the current `index.html`.

## Local Dev Commands

```bash
# Python deps
uv sync

# TUI / REPL
uv run minibot

# Backend (serves API + packaged static UI)
uv run minibot-web --reload

# Frontend dev server (optional)
cd webui
npm install
npm run dev
```

## Reliable WebUI Sync Procedure

When you need to sync a freshly built WebUI into `src/minibot/server/static/`, use this exact flow:

```bash
cd webui
npm run build
mkdir -p ../src/minibot/server/static/assets
cp dist/index.html ../src/minibot/server/static/index.html
cp dist/assets/* ../src/minibot/server/static/assets/
refs=$(grep -oE '/assets/[^" ]+' ../src/minibot/server/static/index.html | sed 's#^/assets/##')
for file in ../src/minibot/server/static/assets/*; do
  [ -e "$file" ] || continue
  base=$(basename "$file")
  keep=false
  for ref in $refs; do
    if [ "$base" = "$ref" ]; then
      keep=true
      break
    fi
  done
  if [ "$keep" = false ]; then
    rm "$file"
  fi
done
ls -1 ../src/minibot/server/static/assets
cat ../src/minibot/server/static/index.html
```

Notes:
- Copy `index.html` and `dist/assets/*` explicitly.
- Then prune `src/minibot/server/static/assets/` by treating `index.html` as the source of truth.
- Final verification rule: the filenames listed by `ls src/minibot/server/static/assets` must exactly match the hashed asset filenames referenced in `src/minibot/server/static/index.html`.

## CI / Release (How Publishing Works)

- **CI**: `.github/workflows/ci.yml`
  - Runs on `push` to `main` + PRs, builds WebUI, copies static assets, runs tests, builds wheel/sdist (sanity).
- **One-click release**: `.github/workflows/publish-pypi.yml`
  - Triggers on `git push` tags `v*`.
  - Enforces: tag version == `pyproject.toml` version.
  - Flow: build WebUI → copy `webui/dist/*` into `src/minibot/server/static/` → prune stale hashed files in `src/minibot/server/static/assets/` → tests → `python -m build` → publish to PyPI (Trusted Publishing; optional `PYPI_API_TOKEN`) → create GitHub Release + upload `dist/*`.

## Session Context Logging (Project Rule)

- Keep a running log under `.claude/tasks/context_session_YYYY-MM-DD.md`.
- When you start work, read today’s context session file.
- When you finish, append what changed, decisions made, and next steps.
