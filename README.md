<div align="center">

# 🤖 MiniBot

<div align="left">

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> A powerful, extensible AI assistant with tool-calling capabilities, multi-agent collaboration, and MCP integration.

**MiniBot** 是一个功能强大的 AI 助手，专为教育目的和开发场景设计。它采用模块化架构，支持工具调用、子代理协作、技能加载和 MCP（Model Context Protocol）集成，让你能够像使用 Claude Code 一样高效地完成各种任务。

---

## ✨ 核心特性

### 🛠️ 强大的工具系统
- **内置工具**: Bash 命令执行、文件读写、任务管理
- **元工具**: 动态加载领域知识（Skill）、生成子代理（Task）
- **工具注册表**: 灵活的工具管理和权限控制
- **安全检查**: 命令危险检测、路径安全验证

### 🤝 多代理协作
- **三种代理类型**: `explore`（只读探索）、`code`（代码实现）、`plan`（策略规划）
- **实时进度**: Rich Live 动态显示子代理执行状态
- **独立权限**: 每个子代理可配置独立的工具访问权限

### 📚 Skills 系统
- **领域知识注入**: 从 `SKILL.md` 加载专业领域指令
- **资源管理**: 支持脚本、参考文档、模板等资源文件
- **自动发现**: 自动检测 `~/.claude/skills` 全局技能目录

### 🔗 MCP 集成
- **多服务器支持**: 同时连接多个 MCP 服务器
- **多种传输方式**: 支持 Stdio 和 SSE 传输
- **无缝适配**: 自动将 MCP 工具适配为 BaseTool
- **资源访问**: 支持访问 MCP 提供的资源

### 🎣 Hooks 系统
- **事件驱动**: 支持工具调用前后、会话开始/结束等事件
- **安全策略**: 内置文件访问限制 Hook
- **可扩展**: 轻松添加自定义 Hook

### 💻 精美的终端体验
- **REPL 交互**: 自然语言对话 + 斜杠命令
- **Rich 美化**: 彩色输出、进度条、状态面板
- **智能补全**: Tab 补全、命令历史
- **多行粘贴**: 优雅的代码块粘贴支持

---

## 🚀 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/minibot.git
cd minibot

# 使用 uv 安装依赖（推荐）
uv sync

# 或使用 pip
pip install -e .
```

### 配置环境变量

创建 `.env` 文件：

```bash
# LLM 配置
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-api-key-here
MODEL_ID=gpt-4o-mini

# 可选配置
MINIBOT_RICH=1              # 启用 Rich 美化输出
MINIBOT_FULL_TOOL_OUTPUT=1  # 显示完整工具输出
```

### 运行

```bash
# 使用 uv
uv run minibot

# 或安装后直接运行
minibot
```

---

## 📖 使用指南

### REPL 命令

进入 REPL 后，你可以使用以下命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/info` | 显示当前配置信息 |
| `/paste` | 进入多行粘贴模式 |
| `/reset` | 重置对话上下文 |
| `/clear` | 清空终端屏幕 |
| `/exit` | 退出程序 |

### 示例对话

```
🤖 MiniBot > 帮我探索一下当前项目的结构

[Agent: explore] 正在探索项目结构...
📂 发现 12 个主要模块
📁 src/minibot/core/ - 核心组件
📁 src/minibot/tools/ - 工具系统
...

🤖 MiniBot > 帮我写一个文件读取工具

[Agent: code] 正在实现文件读取工具...
✅ 已创建 src/minibot/tools/builtin/file.py
✅ 已注册工具到注册表
✅ 工具已就绪

🤖 MiniBot > 使用 task 工具创建一个子代理来分析代码

[Task: analyze-code] 子代理已启动...
🔄 正在分析代码复杂度...
✅ 分析完成，发现 3 个优化点
```

---

## 🏗️ 架构概览

```
minibot/
├── 📁 src/minibot/
│   ├── main.py              # 应用入口，REPL 交互循环
│   ├── agent.py             # 主 Agent 类，协调所有组件
│   ├── 📁 config/           # 配置管理
│   │   ├── schema.py        # 配置数据类
│   │   └── settings.py      # 配置加载
│   ├── 📁 core/             # 核心组件
│   │   ├── types.py         # 类型定义
│   │   └── client.py        # LLM 客户端
│   ├── 📁 tools/            # 工具系统
│   │   ├── base.py          # BaseTool 抽象类
│   │   ├── registry.py      # 工具注册表
│   │   ├── 📁 builtin/      # 内置工具
│   │   └── 📁 meta/         # 元工具
│   ├── 📁 subagents/        # 子代理系统
│   │   ├── registry.py      # 代理类型注册
│   │   └── executor.py      # 子代理执行器
│   ├── 📁 hooks/            # Hooks 系统
│   │   ├── events.py        # 事件定义
│   │   ├── manager.py       # Hook 管理器
│   │   └── executor.py      # Hook 执行器
│   ├── 📁 mcp/              # MCP 客户端
│   │   ├── protocol.py      # 协议类型
│   │   ├── transport.py     # 传输层实现
│   │   ├── client.py        # MCP 客户端
│   │   ├── manager.py       # 多服务器管理
│   │   └── tool_adapter.py  # 工具适配器
│   ├── 📁 skills/           # Skills 加载器
│   │   └── loader.py        # Skill 文件加载器
│   └── 📁 utils/            # 工具函数
├── 📁 config/               # 配置文件目录
│   ├── default.yaml         # 默认配置
│   ├── hooks.yaml           # Hooks 配置
│   └── mcp_servers.yaml     # MCP 服务器定义
├── 📁 hooks/                # Hook 脚本
└── 📁 tests/                # 测试文件
```

---

## 🔧 配置说明

### `config/default.yaml`

```yaml
# Skills 目录
skills_dir: skills

# LLM 配置
llm:
  base_url: ${OPENAI_BASE_URL}
  api_key: ${OPENAI_API_KEY}
  model: ${MODEL_ID:gpt-4o-mini}
  max_tokens: 8000

# 工具配置
tools:
  enabled: ["*"]  # 启用所有工具
  disabled: []
  timeout: 60
```

### `config/hooks.yaml`

```yaml
enabled: true
hooks_dir: hooks

hooks:
  - event: pre_tool_call
    handler: hooks/restrict_file_access.py
    timeout: 5
    enabled: true
```

### `config/mcp_servers.yaml`

```yaml
enabled: true

servers:
  - name: context7
    transport: stdio
    command: npx
    args: ["-y", "@upstash/context7-mcp"]
    enabled: true
```

---

## 🌱 扩展开发

### 添加自定义工具

1. 继承 `BaseTool` 类：

```python
from minibot.tools.base import BaseTool

class MyCustomTool(BaseTool):
    name = "my_tool"
    description = "我的自定义工具"

    async def execute(self, arg1: str, arg2: int = 10):
        # 实现你的逻辑
        return {"result": f"处理完成: {arg1} x {arg2}"}
```

2. 注册工具：

```python
from minibot.tools.registry import tool_registry

tool_registry.register(MyCustomTool())
```

### 创建 Skill

在 `skills/my-skill/` 目录下创建：

```
skills/my-skill/
├── SKILL.md          # 技能说明文档
├── scripts/          # 辅助脚本
├── references/       # 参考文档
└── assets/           # 模板和资源
```

`SKILL.md` 示例：

```markdown
---
name: my-skill
description: 我的自定义技能
---

这是技能的详细说明文档，包含使用指南和最佳实践。
```

### 添加 Hook

在 `hooks/` 目录下创建 Python 脚本：

```python
async def my_hook(event_type, context):
    if event_type == "pre_tool_call":
        # 在工具调用前执行
        print(f"即将调用工具: {context['tool_name']}")
    return True  # 返回 False 可以阻止操作
```

---

## 🔒 安全特性

- **命令安全检测**: 自动识别危险的 Shell 命令
- **路径验证**: 防止目录遍历攻击
- **Hook 系统**: 可配置的安全策略层
- **超时控制**: 防止工具执行时间过长
- **权限隔离**: 子代理独立的工具访问权限

---

## 🛠️ 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 编程语言 |
| asyncio | - | 异步框架 |
| OpenAI API | 1.0+ | LLM 接口 |
| httpx | 0.25+ | HTTP 客户端 |
| Rich | 13.7+ | 终端美化 |
| prompt-toolkit | 3.0+ | 命令行输入 |
| PyYAML | 6.0+ | 配置解析 |
| MCP | - | Model Context Protocol |

---

## 📝 开发路线图

- [ ] 支持更多 LLM 提供商（Anthropic、Google 等）
- [ ] Web UI 界面
- [ ] 插件市场
- [ ] 对话历史持久化
- [ ] 多语言支持
- [ ] 更多内置工具
- [ ] 性能优化和缓存机制

---

## 🤝 贡献指南

欢迎贡献！请遵循以下步骤：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 开发环境设置

```bash
# 安装开发依赖
uv sync --dev

# 运行测试
uv run pytest

# 代码格式化
u run black src/
```

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- [Claude Code](https://claude.ai/code) - 灵感来源
- [OpenAI](https://openai.com/) - LLM API 支持
- [Model Context Protocol](https://modelcontextprotocol.io/) - MCP 规范
- [Rich](https://rich.readthedocs.io/) - 终端美化库

---

## 📮 联系方式

- 作者: zyren
- 邮箱: ren990603@gmail.com
- 项目链接: [https://github.com/yourusername/minibot](https://github.com/yourusername/minibot)

---

<div align="center">

**如果这个项目对你有帮助，请给它一个 ⭐️**

Made with ❤️ by MiniBot Team

</div>