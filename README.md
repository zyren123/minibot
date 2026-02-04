## MiniBot

Python 3.12 的命令行 Coding Agent（Claude Code 风格），支持 Tools / Skills / Subagents / Hooks / MCP。

### 运行

```bash
uv sync
uv run minibot
```

### REPL 交互

- 输入自然语言开始对话
- `/help` 查看可用命令（支持 Tab 补全，若系统有 `readline`）
- `/paste` 进入多行粘贴模式（用单独一行 `.` 结束）
- `/reset` 清空当前会话 history
- `/clear` 清屏

### 输出

- Tool 输出默认截断；设置 `MINIBOT_FULL_TOOL_OUTPUT=1` 可显示完整输出
- 默认使用 Rich 美化输出与动态刷新；设置 `MINIBOT_RICH=0` 可关闭（或在非 TTY 环境自动降级）
