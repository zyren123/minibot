# AGENTS.md

This file is a **permanent memory / onboarding note for coding agents** working on this repo.

## Quick Facts (Don’t Re-Learn From Scratch)

- **PyPI distribution name**: `minibotclaw`
  - **Import package** stays: `minibot`
  - **CLI scripts**: `minibot`, `minibot-web`
- **Release automation**: pushing a version tag `vX.Y.Z` triggers **PyPI publish + GitHub Release** via `.github/workflows/publish-pypi.yml`.
  - Do **not** rename/move `.github/workflows/publish-pypi.yml` unless you also update the PyPI Trusted Publisher config.
- **WebUI build**: `webui/` (Vite) builds to `webui/dist/` and is copied into `src/minibot/server/static/` **before packaging**.

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

## CI / Release (How Publishing Works)

- **CI**: `.github/workflows/ci.yml`
  - Runs on `push` to `main` + PRs, builds WebUI, copies static assets, runs tests, builds wheel/sdist (sanity).
- **One-click release**: `.github/workflows/publish-pypi.yml`
  - Triggers on `git push` tags `v*`.
  - Enforces: tag version == `pyproject.toml` version.
  - Flow: build WebUI → copy `webui/dist/*` into `src/minibot/server/static/` → tests → `python -m build` → publish to PyPI (Trusted Publishing; optional `PYPI_API_TOKEN`) → create GitHub Release + upload `dist/*`.

## Session Context Logging (Project Rule)

- Keep a running log under `.claude/tasks/context_session_YYYY-MM-DD.md`.
- When you start work, read today’s context session file.
- When you finish, append what changed, decisions made, and next steps.

