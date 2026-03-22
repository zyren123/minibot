<div align="center">

# MiniBot

### A Minimalist, Framework-Free AI Agent Implementation in Pure Python

<div align="left">

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![No Frameworks](https://img.shields.io/badge/Framework-None-crimson?style=flat-square)](https://github.com/zyren123/minibot)
[![Architecture](https://img.shields.io/badge/Architecture-Event--Driven-blueviolet?style=flat-square)](https://github.com/zyren123/minibot)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Code Style](https://img.shields.io/badge/Code%20Style-Black-000000?style=flat-square)](https://github.com/psf/black)

> **拒绝臃肿的抽象层。** MiniBot 是一个用于教学和研究的 AI Agent 项目。它不依赖 LangChain、AutoGen 或 langgraph等框架，而是使用纯 Python 原生代码展示了 Agent 的核心逻辑、工具调用、MCP 协议集成以及 Hook 系统的底层实现。

**[ 核心理念 ]** &nbsp; `纯原生实现` • `透明可控` • `MCP 协议` • `插件化架构`

[中文](README.md) | [English](docs/README.en.md)

</div>
</div>

---

<div style="display: flex; justify-content: center; align-items: flex-start; gap: 10px;">
  <img style="height: 400px; width: auto;" alt="Claude Code Minibot" src="https://github.com/user-attachments/assets/c4c1cc8e-9c9a-44e0-ab15-2981fa921cea" />
  <img style="height: 400px; width: auto;" alt="Paraglider Minibot" src="https://github.com/user-attachments/assets/7e221968-293b-4324-9a52-9e6ce26c4be9" />
</div>
<p align="center" style="margin-top: 15px; font-size: 1.2em; font-weight: bold; color: #555;">
  最起码......我们都是机器人助手，不是吗？
</p>

---

## 🧐 为什么开发 MiniBot？

在当今的 AI 开发中，我们被各种框架包裹（LangChain, AutoGen 等），导致很多开发者或感兴趣的朋友并不清楚 Agent **到底是如何工作**的。

MiniBot 的目标是 **"De-mystify Agents"（揭秘 Agent）**。通过阅读本项目源码，你将理解：

1.  **ReAct 循环的本质**：如何用原生 Python `while` 循环和 OpenAI API 构建思考-行动链。
2.  **工具调用的底层逻辑**：如何通过 Python `inspect` 模块将函数自动转换为 JSON Schema。
3.  **MCP (Model Context Protocol)**：如何在不依赖官方 SDK 的情况下实现 Client 端协议握手。
4.  **Skills 上下文管理**：如何动态地从文件系统加载 Prompts 和知识库。

---

## ⚡ 核心架构与实现

MiniBot 采用极其精简的模块化设计，没有任何复杂的类继承链。

### 1. 🔍 Agent Core (无框架 ReAct)
摒弃复杂的 Chain/Graph 抽象，回归本质。
- **实现**：`src/minibot/agent.py`
- **逻辑**：维护一个纯粹的 `List[Message]` 消息队列，通过递归或循环处理 LLM 的 `tool_calls` 响应。

### 2. 🛠️ Native Tool System (原生工具链)
不使用 Pydantic 生成 Schema，而是直接解析 Python 函数签名。
- **实现**：`src/minibot/tools/`
- **特性**：支持 `Bash` 执行、文件 IO，以及动态注册机制。支持 **元工具 (Meta-Tools)**，即“创造工具的工具”。

### 3. 🔗 MCP Integration (模型上下文协议)
完全兼容 Claude 的 MCP 协议，连接万物。
- **实现**：`src/minibot/mcp/`
- **亮点**：实现了基于 `stdio` 和 `sse` 的传输层，自动将 MCP 资源适配为 Agent 可调用的 Tools。

### 4. 🎣 Hooks & Lifecycle (生命周期钩子)
基于简单的观察者模式实现的安全与监控层。
- **实现**：`src/minibot/hooks/`
- **用途**：在 `pre_tool_call` 拦截高危命令，在 `post_agent_loop` 记录审计日志。

### 5. 📚 Skill Loader (动态技能)
- **实现**：`src/minibot/skills/`
- **逻辑**：类似于 Claude 的 Project，自动读取 Markdown 文件并注入 System Prompt。

---

## 🚀 快速上手

我们使用 `uv` 进行现代化的 Python 包管理（当然也支持 pip）。

### 安装

```bash
uv tool install minibotclaw

# 运行 REPL
minibot

# 启动 WebUI
minibot-web
```

升级：

```bash
uv tool upgrade minibotclaw
```

从源码安装（开发用）：

```bash
git clone https://github.com/zyren123/minibot.git
cd minibot

# 极速安装依赖
uv sync

# WebUI 静态资源不再提交到仓库，源码运行前需要先构建一次
cd webui
npm install
npm run build
cd ..
```

### 配置

复制 `.env.example` 到 `.env`：

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxxx
MODEL_ID=gpt-4o-mini
# 可选：开启 Rich 终端美化，体验类似 Claude Code
MINIBOT_RICH=1
```

### 启动 REPL

```bash
# PyPI / uv tool 安装
minibot

# 源码安装（uv sync）
uv run minibot
```

常用命令：

- `/help`
- `/info`
- `/stream [on|off|status]`

### 启动 WebUI（本机）

```bash
# PyPI / uv tool 安装
minibot-web

# 源码安装（需先执行一次 webui/npm run build）
uv run minibot-web
```

然后打开：`http://127.0.0.1:7860/`

开发模式（前后端分离）：

```bash
# Terminal A
uv run minibot-web --reload

# Terminal B
cd webui
npm install
npm run dev
```

### 飞书平台接入（WebSocket）

1. 在飞书开放平台创建企业自建应用，并开启机器人能力。
2. 为应用订阅 `im.message.receive_v1` 事件，并授予发送消息所需权限。
3. 启动 `minibot-web`，打开 WebUI 的 `Platforms` 标签页。
4. 新建一个 `Feishu` 平台连接，填写 `App ID`、`App Secret`，并绑定到目标 Bot。
5. 如果删除了该 Bot，平台连接会自动改绑到默认 `Minibot`，并从新会话继续处理后续消息。

当前限制：
- 仅支持飞书私聊文本消息。
- 使用长连接 WebSocket 模式，不依赖公网 webhook。
- 回复以最终文本消息返回，不做流式 token 推送或消息卡片。

### SDK 使用（Python）

非流式：

```python
from minibot import Minibot

agent = Minibot(system_prompt="你是一个中文助手。")
result = agent.chat_sync("你好")
print(result.assistant_text)
```

流式（事件）：

```python
import asyncio
from minibot import Minibot

async def main():
    agent = Minibot()
    async for ev in agent.stream("给我讲个笑话"):
        if ev.get("type") == "assistant_delta":
            print(ev.get("delta_text", ""), end="", flush=True)

asyncio.run(main())
```

注册自定义工具（直接传 Python function）：

```python
from minibot import Minibot

def echo(text: str) -> str:
    return text

agent = Minibot(tools=[echo])
```

---

## 💻 源码导读 (Where to Learn)

这是一份学习指南，告诉你代码的每一部分展示了什么概念：

```text
src/minibot/
├── agent.py             # [核心] 看这里理解 LLM 的“思考-执行”循环是如何手写的
├── core/
│   └── client.py        # 封装 OpenAI SDK，处理流式输出和多模态
├── tools/
│   ├── base.py          # [重点] 如何用 inspect 库将 Python 函数转为 JSON Schema
│   └── registry.py      # 简单的字典查找表，实现工具分发
├── mcp/
│   ├── client.py        # [进阶] 手写 MCP 协议客户端，理解 JSON-RPC 2.0
│   └── transport.py     # 进程间通信 (Stdio/SSE) 的实现
├── skills/
│   └── loader.py        # 如何解析文件系统并动态构建 Prompt 上下文
└── hooks/
    └── executor.py      # 中间件模式的实现，用于安全拦截
```

---

## 🎮 交互示例

MiniBot 提供了一个基于 `prompt_toolkit` 和 `Rich` 的现代化终端界面。 没错，readme就是用minibot写的

<img width="1310" height="1176" alt="image" src="https://github.com/user-attachments/assets/cbe0ad45-8ec9-40c5-b32c-8c8a0b9634ef" />


---

## 🔧 扩展开发

### 1. 编写一个纯 Python 工具

不需要继承复杂的类，只需定义函数和类型注解：

```python
from minibot.tools.base import BaseTool

class WeatherTool(BaseTool):
    name = "get_weather"
    description = "获取城市天气"

    # 类型注解会自动转换为 Tool Schema
    async def execute(self, city: str, unit: str = "celsius") -> str:
        # 这里写原生 Python 逻辑
        return f"{city} 的天气是 25度 ({unit})"
```

### 2. 接入 MCP Server

在 `config/mcp_servers.yaml` 中配置，无需改代码即可扩展能力（例如连接 GitHub, Postgres 等）：

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

MiniBot 现已支持 **in-process Agent Teams**（会话内团队编排）：

- Lead 可通过工具自主决定是否创建团队、创建多少成员（默认 3，最大 6）
- Teammate 拥有完整工作能力（读写文件、bash、MCP、memory 等），但**不能再创建成员**（禁用 `Task`/`TeamCreate`/`TeamShutdown`）
- 任意成员可点对点通信（`TeamMessage`）或全队广播（`TeamBroadcast`）
- 提供轻量共享任务板（`TeamTask`：create/list/assign/claim/complete）
- 提供 `TeamWait` 用于 Lead 等待并汇总队友事件

### 可用 Team 工具

- `TeamCreate`
- `TeamMembers`
- `TeamTask`
- `TeamMessage`
- `TeamBroadcast`
- `TeamWait` (lead only)
- `TeamShutdown` (lead only)

### 当前限制

- 仅支持 **单会话内** 团队，不支持跨重启恢复
- 不支持 tmux/iTerm2 分屏模式（MVP 仅 in-process）
- 不支持嵌套团队（teammate 不可再派生代理）

### 相关配置

`config/default.yaml`:

```yaml
llm:
  stream_enabled: true

teams:
  quiet_teammates: true
  debug_teammate_output: false
```

开启后 teammate 不会向终端输出 Thinking/Running 状态行与常规内容，避免并发输出污染主终端。
主 Agent（solo/lead）默认开启流式正文输出；若网关不支持流式，会自动回退为非流式输出。

---

## 🗺️ Roadmap

- [x] **长期记忆支持**: 基于本地文件系统的持久化上下文记忆
- [x] **Agent Teams (MVP)**: 会话内并发团队、消息总线、任务板、锁冲突保护
- [ ] **Vision**: 原生支持多模态图像理解
- [ ] **Sandboxing**: 基于 Docker 的工具执行沙箱
- [ ] **Web Interface**: 基于 FastAPI 的轻量级 API

---

## 🤝 贡献与协议

本项目采用 [MIT License](LICENSE)。

欢迎提交 PR！如果你想学习 Agent 原理，最好的方式就是尝试修改 `src/minibot/agent.py` 中的主循环逻辑。

---

<div align="center">
Made with ❤️ by Engineers, for Engineers.
</div>
