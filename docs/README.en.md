<div align="center">

# MiniBot

### A learnable, extensible, local-first AI agent built in pure Python

<div align="left">

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/minibotclaw?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/minibotclaw/)
[![CI](https://img.shields.io/github/actions/workflow/status/zyren123/minibot/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/zyren123/minibot/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](../LICENSE)
[![Architecture](https://img.shields.io/badge/Architecture-Event--Driven-blueviolet?style=flat-square)](https://github.com/zyren123/minibot)

> **Run it first, understand it next.** MiniBot gives you a REPL, WebUI, Python SDK, MCP, Skills, Hooks, Memory, and Team orchestration in one local-first project, while keeping the implementation transparent enough to study and reshape.

**[ You can use it for ]** &nbsp; `Local REPL` • `WebUI` • `Python SDK` • `MCP/Skills` • `Multi-agent teamwork`

[中文](../README.md) | English

</div>
</div>

---

<div style="display: flex; justify-content: center; align-items: flex-start; gap: 10px;">
  <img style="height: 400px; width: auto;" alt="Claude Code Minibot" src="https://github.com/user-attachments/assets/c4c1cc8e-9c9a-44e0-ab15-2981fa921cea" />
  <img style="height: 400px; width: auto;" alt="Paraglider Minibot" src="https://github.com/user-attachments/assets/7e221968-293b-4324-9a52-9e6ce26c4be9" />
</div>
<p align="center" style="margin-top: 15px; font-size: 1.2em; font-weight: bold; color: #555;">
  MiniBot covers the path from terminal workflows to WebUI demos to SDK embedding in your own apps.
</p>

---

## ⚡ What Can MiniBot Help You Do?

- Start a local interactive agent REPL with `minibot`.
- Launch a WebUI and API server with `minibot-web` for multi-turn chat, session management, and platform integrations.
- Embed an agent directly in Python via `from minibot import Minibot`.
- Extend capabilities with MCP, Skills, Hooks, Memory, and Team tools instead of depending on a heavyweight framework stack.

## 🎯 Why This Instead of Another Agent Demo?

- **Real entry points, not just architecture talk**: terminal, WebUI, and SDK are all runnable today.
- **Readable source code**: no LangChain, AutoGen, or LangGraph dependency layers between you and the core loop.
- **Local-first control**: workdir, skills, memory, hooks, and MCP configuration all live in files you can inspect and change.

## 🚀 30-Second Start

```bash
uv tool install minibotclaw

# Complete model setup after first launch
# Terminal REPL
minibot

# WebUI
minibot-web
```

You need model provider credentials

- WebUI: add a Provider in `Config`, import models, and choose a chat model for the Bot.
- CLI: run `/model config` after launch to edit the global `.env` interactively.
- Manual: edit the global `.env` directly. The default path is `~/.minibot/.env`.

Minimum required fields:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxxx
MODEL_ID=gpt-4o-mini
```

## 👀 Who It Fits, And Who It Does Not

Use MiniBot if you want to:

- Understand how agent loops, tool calling, MCP, and skills actually work.
- Prototype a local, controllable, hackable agent without getting buried under framework abstractions.
- Connect CLI, WebUI, SDK, and platform integrations inside one Python project.

MiniBot is probably not the right fit if you want:

- A production-hardened sandbox and strict permission model out of the box.
- A fully productized platform for non-technical end users.
- A batteries-included framework ecosystem instead of a readable implementation.

---

## 🧐 Why MiniBot?

In today's AI development, we're surrounded by frameworks (LangChain, AutoGen, etc.), leaving many developers unclear about **how Agents actually work**.

MiniBot aims to **"De-mystify Agents"**. By reading this project's source code, you will understand:

1.  **The essence of the ReAct loop**: How to build a think-act chain using native Python `while` loops and the OpenAI API.
2.  **The underlying logic of tool invocation**: How to automatically convert functions to JSON Schema using Python's `inspect` module.
3.  **MCP (Model Context Protocol)**: How to implement Client-side protocol handshake without relying on official SDKs.
4.  **Skills context management**: How to dynamically load Prompts and knowledge bases from the file system.

---

## ⚡ Core Architecture & Implementation

MiniBot uses an extremely lean modular design with no complex class inheritance chains.

### 1. 🔍 Agent Core (Framework-Free ReAct)
Abandon complex Chain/Graph abstractions and return to fundamentals.
- **Implementation**: `src/minibot/agent.py`
- **Logic**: Maintains a pure `List[Message]` message queue, processing LLM `tool_calls` responses via recursion or loops.

### 2. 🛠️ Native Tool System (Native Toolchain)
No Pydantic for Schema generation—direct parsing of Python function signatures.
- **Implementation**: `src/minibot/tools/`
- **Features**: Supports `Bash` execution, file I/O, and dynamic registration. Supports **Meta-Tools**—tools that create tools.

### 3. 🔗 MCP Integration (Model Context Protocol)
Fully compatible with Claude's MCP protocol, connecting everything.
- **Implementation**: `src/minibot/mcp/`
- **Highlights**: Implements transport layers based on `stdio` and `sse`, automatically adapting MCP resources into Agent-callable Tools.

### 4. 🎣 Hooks & Lifecycle (Lifecycle Hooks)
A security and monitoring layer based on a simple observer pattern.
- **Implementation**: `src/minibot/hooks/`
- **Use cases**: Intercept high-risk commands at `pre_tool_call`, record audit logs at `post_agent_loop`.

### 5. 📚 Skill Loader (Dynamic Skills)
- **Implementation**: `src/minibot/skills/`
- **Logic**: Similar to Claude's Project—automatically reads Markdown files and injects them into the System Prompt.

---

## 🚀 Quick Start

We use `uv` for modern Python package management (pip is also supported).

### Installation

```bash
uv tool install minibotclaw

# Run REPL
minibot

# Launch WebUI
minibot-web
```

Upgrade:

```bash
uv tool upgrade minibotclaw
```

Install from source (development):

```bash
git clone https://github.com/zyren123/minibot.git
cd minibot

# Fast dependency installation
uv sync

# WebUI static assets are not committed to the repo, so build them once before running from source
cd webui
npm install
npm run build
cd ..
```

### Configuration

On first launch, MiniBot auto-creates a global `.env` in the app home directory. By default that path is `~/.minibot/.env`.

You can finish setup through the WebUI `Config` page or the CLI `/model config` flow. If you prefer manual editing, the minimum fields are:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxxx
MODEL_ID=gpt-4o-mini
# Optional: Enable Rich terminal styling for a Claude Code-like experience
MINIBOT_RICH=1
```

### Launch REPL

```bash
# PyPI / uv tool install
minibot

# Source install (uv sync)
uv run minibot
```

Common commands:

- `/help`
- `/info`
- `/stream [on|off|status]`

---

## 💻 Source Code Guide (Where to Learn)

A learning guide showing what each part of the code demonstrates:

```text
src/minibot/
├── agent.py             # [Core] Understand how the LLM "think-execute" loop is hand-written
├── core/
│   └── client.py        # Wraps OpenAI SDK, handles streaming and multimodal
├── tools/
│   ├── base.py          # [Key] How to convert Python functions to JSON Schema using inspect
│   └── registry.py      # Simple dict lookup for tool dispatch
├── mcp/
│   ├── client.py        # [Advanced] Hand-written MCP protocol client, understand JSON-RPC 2.0
│   └── transport.py     # Inter-process communication (Stdio/SSE) implementation
├── skills/
│   └── loader.py        # How to parse file system and dynamically build Prompt context
└── hooks/
    └── executor.py      # Middleware pattern for security interception
```

---

## 🎮 Interactive Demo

MiniBot provides a modern terminal interface based on `prompt_toolkit` and `Rich`. Yes, this README was written with MiniBot.

<img width="1310" height="1176" alt="image" src="https://github.com/user-attachments/assets/cbe0ad45-8ec9-40c5-b32c-8c8a0b9634ef" />

---

## 🔧 Extending MiniBot

### 1. Writing a Pure Python Tool

No complex class inheritance—just define functions with type annotations:

```python
from minibot.tools.base import BaseTool

class WeatherTool(BaseTool):
    name = "get_weather"
    description = "Get city weather"

    # Type annotations are automatically converted to Tool Schema
    async def execute(self, city: str, unit: str = "celsius") -> str:
        # Native Python logic here
        return f"Weather in {city}: 25° ({unit})"
```

### 2. Connecting an MCP Server

Configure in `config/mcp_servers.yaml`—extend capabilities without code changes (e.g., GitHub, Postgres):

```yaml
servers:
  - name: github-mcp
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "your-token"
```

---

## 👥 Agent Teams (MVP)

MiniBot now supports **in-process Agent Teams** (session-internal team orchestration):

- Lead can autonomously decide whether to create a team and how many members (default 3, max 6)
- Teammates have full capabilities (read/write files, bash, MCP, memory, etc.) but **cannot create members** (Task/TeamCreate/TeamShutdown disabled)
- Any member can send point-to-point messages (`TeamMessage`) or broadcast to the team (`TeamBroadcast`)
- Lightweight shared task board (`TeamTask`: create/list/assign/claim/complete)
- `TeamWait` for Lead to wait and aggregate teammate events

### Available Team Tools

- `TeamCreate`
- `TeamMembers`
- `TeamTask`
- `TeamMessage`
- `TeamBroadcast`
- `TeamWait` (lead only)
- `TeamShutdown` (lead only)

### Current Limitations

- **Single-session** teams only—no cross-restart recovery
- No tmux/iTerm2 split-screen mode (MVP is in-process only)
- No nested teams (teammates cannot spawn sub-agents)

### Related Configuration

`config/default.yaml`:

```yaml
llm:
  stream_enabled: true

teams:
  quiet_teammates: true
  debug_teammate_output: false
```

When enabled, teammates won't output Thinking/Running status lines or regular content to the terminal, avoiding concurrent output pollution.
The main Agent (solo/lead) has streaming body output enabled by default; if the gateway doesn't support streaming, it falls back to non-streaming output.

---

## 🗺️ Roadmap

- [x] **Long-term memory**: Persistent context memory based on local file system
- [x] **Agent Teams (MVP)**: In-session concurrent teams, message bus, task board, lock conflict protection
- [ ] **Vision**: Native multimodal image understanding
- [ ] **Sandboxing**: Docker-based tool execution sandbox
- [ ] **Web Interface**: Lightweight FastAPI-based API

---

## 🤝 Contributing & License

This project is licensed under [MIT License](../LICENSE).

PRs are welcome! If you want to learn Agent principles, the best way is to try modifying the main loop logic in `src/minibot/agent.py`.

---

<div align="center">
Made with ❤️ by Engineers, for Engineers.
</div>
