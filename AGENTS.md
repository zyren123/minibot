# AGENTS.md

This file is a **permanent memory / onboarding note for coding agents** working on this repo.

## Quick Facts (Don’t Re-Learn From Scratch)

- **PyPI distribution name**: `minibotclaw`
  - **Import package** stays: `minibot`
  - **CLI scripts**: `minibot`, `minibot-web`
- **Release automation**: pushing a version tag `vX.Y.Z` triggers **PyPI publish + GitHub Release** via `.github/workflows/publish-pypi.yml`.
  - Do **not** rename/move `.github/workflows/publish-pypi.yml` unless you also update the PyPI Trusted Publisher config.
- **WebUI build**: `webui/` (Vite) builds to `webui/dist/` and is copied into `src/minibot/server/static/` **before packaging**.
  - Use `bash scripts/sync_webui_static.sh` to sync `webui/dist/` into `src/minibot/server/static/`.
  - The sync script is responsible for pruning stale hashed files in `src/minibot/server/static/assets/` so only files referenced by the current `index.html` remain.
  - After building archives, use `python scripts/verify_package_static.py dist/*.whl dist/*.tar.gz` to confirm the packaged WebUI files are actually present.

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
- `src/minibot/server/static/` is treated as a build artifact directory for packaging.
  - Do not rely on ad hoc copy commands; use `bash scripts/sync_webui_static.sh` so `index.html` and hashed assets stay in sync.
  - Project rule: after each build sync, `src/minibot/server/static/assets/` must contain only the files referenced by the current `index.html`.

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

When you need to sync a freshly built WebUI into `src/minibot/server/static/`, use the repo script:

```bash
cd webui
npm run build
cd ..
bash scripts/sync_webui_static.sh
```

Notes:
- `scripts/sync_webui_static.sh` is the source of truth for the sync/prune flow.
- The script verifies that the filenames in `src/minibot/server/static/assets/` exactly match the hashed asset filenames referenced in `src/minibot/server/static/index.html`.
- When validating built archives, run `python scripts/verify_package_static.py dist/*.whl dist/*.tar.gz`.

## CI / Release (How Publishing Works)

- **CI**: `.github/workflows/ci.yml`
  - Runs on `push` to `main` + PRs, builds WebUI, runs `bash scripts/sync_webui_static.sh`, runs tests, builds wheel/sdist, then verifies packaged static assets.
- **One-click release**: `.github/workflows/publish-pypi.yml`
  - Triggers on `git push` tags `v*`.
  - Enforces: tag version == `pyproject.toml` version.
  - Flow: build WebUI → run `bash scripts/sync_webui_static.sh` → tests → `python -m build` → run `python scripts/verify_package_static.py dist/*.whl dist/*.tar.gz` → publish to PyPI (Trusted Publishing; optional `PYPI_API_TOKEN`) → create GitHub Release + upload `dist/*`.

## Cross-Platform Test Stability

- Linux CI is case-sensitive even when macOS local dev is not. When tests create files that production code later reads, use the **exact** filename casing from the implementation. Recent failure example: memory long-term storage uses `LONG_TERM.md`, so a test that wrote `long_term.md` passed locally on macOS but failed in GitHub Actions.
- Do not hard-code "today" filenames in tests for daily/session/memory files unless the test explicitly freezes time. Prefer deriving the date at runtime (for example `datetime.now().strftime("%Y-%m-%d")`) or passing an explicit date through the product API.
- If a filesystem-related test passes locally but fails in GitHub Actions, check filename case, date assumptions, and other OS-sensitive path behaviors before suspecting the agent/prompt/business logic.

## Cross-Platform Text I/O Rules

- Treat all repo-owned text files as UTF-8. Always pass `encoding="utf-8"` when reading or writing `SKILL.md`, Markdown docs, YAML/JSON/TOML config, prompt templates, session files, and other project-managed text artifacts.
- Do not use bare `open(...)`, `Path.read_text()`, or `Path.write_text()` for repo-owned text files. If a call site is intentionally using the platform default encoding, leave a short comment explaining why.
- For arbitrary user files or third-party files, choose a decoding strategy explicitly. Prefer UTF-8 first, then apply a deliberate fallback only when the product behavior requires it.
- When touching skills, config loading, prompts, sessions, or chat/server startup paths, add or update at least one regression test that would fail if the explicit encoding argument were removed.
- During review, grep touched Python code for bare `open(`, `read_text()`, and `write_text()` and verify each text I/O call has an intentional encoding choice.
- If a bug reproduces on Windows but not macOS/Linux, check text encoding, path normalization, and CRLF behavior early before assuming the issue is in the model or API layer.
- Prefer ASCII punctuation in repo instructions and templates unless Unicode is necessary. This keeps logs and diagnostics readable even in terminals that are not configured for UTF-8.

## Session Context Logging (Project Rule)

- Keep a running log under `.claude/tasks/context_session_YYYY-MM-DD.md`.
- When you start work, read today’s context session file.
- When you finish, append what changed, decisions made, and next steps.
