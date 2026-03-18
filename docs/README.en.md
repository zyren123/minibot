<div align="center">

# MiniBot

### A Minimalist, Framework-Free AI Agent Implementation in Pure Python

<div align="left">

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![No Frameworks](https://img.shields.io/badge/Framework-None-crimson?style=flat-square)](https://github.com/zyren123/minibot)
[![Architecture](https://img.shields.io/badge/Architecture-Event--Driven-blueviolet?style=flat-square)](https://github.com/zyren123/minibot)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](../LICENSE)
[![Code Style](https://img.shields.io/badge/Code%20Style-Black-000000?style=flat-square)](https://github.com/psf/black)

> **No bloated abstraction layers.** MiniBot is an AI Agent project for teaching and research. It does not rely on frameworks like LangChain, AutoGen, or LangGraph—instead, it uses pure Python to demonstrate the core logic of Agents, tool invocation, MCP protocol integration, and the underlying implementation of the Hook system.

**[ Core Philosophy ]** &nbsp; `Pure Native Implementation` • `Transparent & Controllable` • `MCP Protocol` • `Plugin Architecture`

[中文](../README.md) | English

</div>
</div>

---

<div style="display: flex; justify-content: center; align-items: flex-start; gap: 10px;">
  <img style="height: 400px; width: auto;" alt="Claude Code Minibot" src="https://github.com/user-attachments/assets/c4c1cc8e-9c9a-44e0-ab15-2981fa921cea" />
  <img style="height: 400px; width: auto;" alt="Paraglider Minibot" src="https://github.com/user-attachments/assets/7e221968-293b-4324-9a52-9e6ce26c4be9" />
</div>
<p align="center" style="margin-top: 15px; font-size: 1.2em; font-weight: bold; color: #555;">
  At least... we're all robot assistants, right?
</p>

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
```

### Configuration

Copy `.env.example` to `.env`:

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
