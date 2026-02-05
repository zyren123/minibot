# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Minibot is a modular Claude Code clone written in Python 3.12. It features:
- **Modular architecture** - Clean separation of concerns
- **Skills system** - Load domain knowledge on-demand via SKILL.md files
- **Subagents** - Spawn focused agents for subtasks (explore, code, plan)
- **Hooks system** - Execute custom scripts on events (pre/post tool calls, session start/end)
- **MCP client** - Connect to external MCP servers for extended tool capabilities
- **Interactive REPL** - prompt-toolkit powered input with history and auto-completion

## Development Commands

```bash
# Install dependencies
uv sync

# Run the application
uv run python -m minibot

# Or use the entry point (after install)
minibot

# Add a new dependency
uv add <package-name>
```

## Project Structure

```
minibot/
├── config/                   # Configuration files
│   ├── default.yaml          # Default settings
│   ├── hooks.yaml            # Hooks configuration
│   └── mcp_servers.yaml       # MCP server definitions
├── hooks/                    # User hook scripts
│   └── examples/              # Example hooks
├── skills/                   # SKILL.md files
├── src/minibot/              # Main package
│   ├── __init__.py
│   ├── main.py               # Entry point with REPL
│   ├── agent.py              # Main Agent class (async)
│   ├── config/               # Configuration management
│   │   ├── schema.py         # Config dataclasses
│   │   └── settings.py       # Config loading
│   ├── core/                 # Core components
│   │   ├── types.py          # Type definitions
│   │   └── client.py         # LLM client wrapper
│   ├── tools/                # Tool system
│   │   ├── base.py           # BaseTool abstract class
│   │   ├── registry.py       # Tool registry
│   │   ├── builtin/          # Built-in tools
│   │   │   ├── bash.py        # Shell command execution
│   │   │   ├── file.py        # File read/write/edit
│   │   │   └── todo.py        # Task management
│   │   └── meta/             # Meta tools
│   │       ├── skill.py       # Skill loader
│   │       └── task.py        # Subagent spawner
│   ├── skills/               # Skill loader
│   │   └── loader.py
│   ├── subagents/            # Subagent system
│   │   ├── registry.py       # Agent type registry
│   │   └── executor.py       # Subagent execution engine
│   ├── hooks/                # Hooks system
│   │   ├── events.py         # Event definitions
│   │   ├── manager.py        # Hook manager
│   │   └── executor.py       # Hook executor
│   ├── mcp/                  # MCP client
│   │   ├── protocol.py       # Protocol types
│   │   ├── transport.py      # Stdio/SSE transports
│   │   ├── client.py         # MCP client
│   │   ├── manager.py        # Multi-server manager
│   │   └── tool_adapter.py   # MCP-to-BaseTool adapter
│   └── utils/                # Utilities
│       ├── output.py         # Rich output formatting
│       ├── prompt_toolkit.py # Interactive input
│       ├── rich_progress.py  # Progress bars
│       └── terminal.py       # Terminal utilities
└── tests/                    # Test files
```

## Key Components

### Tools
| Tool | Description |
|------|-------------|
| `bash` | Execute shell commands |
| `read_file` | Read file contents |
| `write_file` | Write/overwrite files |
| `edit_file` | Replace text in files |
| `TodoWrite` | Manage task list |
| `Skill` | Load skills dynamically |
| `Task` | Spawn subagents |

### Adding a New Tool
1. Create a class extending `BaseTool` in `src/minibot/tools/builtin/`
2. Implement `name`, `description`, `input_schema`, and `execute()`
3. Register in `Agent._register_tools()`

### Adding a Skill
Create a folder in `skills/` with a `SKILL.md` file:
```markdown
---
name: my-skill
description: What this skill does
---

# My Skill Instructions
...
```
Minibot also auto-detects skills stored in `~/.claude/skills` if that directory exists.

### Adding a Subagent
1. Create a new agent class in `src/minibot/subagents/`
2. Register in `SubagentExecutor.__init__()` or via decorator
3. Available agent types: `Explore`, `Plan`, `general-purpose`, `code-reviewer`

### Configuring Hooks
Edit `config/hooks.yaml`:
```yaml
hooks:
  - event: pre_tool_call
    handler: hooks/my_hook.py
    timeout: 5
```

### Configuring MCP Servers
Edit `config/mcp_servers.yaml`:
```yaml
servers:
  - name: my-server
    transport: stdio
    command: npx
    args: ["-y", "@my/mcp-server"]
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENAI_BASE_URL` | LLM API base URL |
| `OPENAI_API_KEY` | API key |
| `MODEL_ID` | Model identifier (default: gpt-4.1-mini) |

## Rules
- Before you do any work, MUST view file in `.claude/tasks/context_session_[date].md` to get full context
- `context_session_x.md` should contain most context of what we did, overall plan, and sub agents will continuously add context
- After you finish the work, MUST update the `.claude/tasks/context_session_[date].md` file
