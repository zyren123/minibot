"""Minibot application entry point."""

import asyncio
import atexit
from pathlib import Path

from .config import load_config
from .agent import Agent
from .utils.output import clear_screen, print_panel, print_system, prompt_input, prompt_input_markup
from .utils.rich_utils import rich_enabled, get_console


SLASH_COMMANDS = {
    "/help",
    "/clear",
    "/exit",
    "/quit",
    "/info",
    "/reset",
    "/paste",
}


def _setup_readline(history_path: Path) -> None:
    try:
        import readline  # noqa: F401
    except Exception:
        return

    try:
        import readline

        readline.parse_and_bind("tab: complete")

        def completer(text: str, state: int) -> str | None:
            matches = sorted([c for c in SLASH_COMMANDS if c.startswith(text)])
            if state < len(matches):
                return matches[state]
            return None

        readline.set_completer(completer)
    except Exception:
        pass

    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        if history_path.exists():
            import readline

            readline.read_history_file(str(history_path))
    except Exception:
        pass

    def _save_history() -> None:
        try:
            import readline

            readline.write_history_file(str(history_path))
        except Exception:
            return

    atexit.register(_save_history)


def _read_paste() -> str:
    print_system("Paste mode: enter text, finish with a single '.' on its own line.")
    lines: list[str] = []
    while True:
        try:
            line = prompt_input("… ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _print_banner(agent: Agent) -> None:
    info = agent.get_info()
    lines = [
        f"workdir: {info['workdir']}",
        f"model: {info['model']}  session: {info['session_id']}",
        f"skills: {', '.join(info['skills']) or 'none'}",
        f"agent types: {', '.join(info['agent_types'])}",
    ]
    if info.get("mcp_servers"):
        lines.append(f"mcp: {', '.join(info['mcp_servers'])}  tools: {info['mcp_tools']}")
    lines.append("")
    lines.append("Tips: /help  /paste  /reset  /clear  exit")
    print_panel("MiniBot", "\n".join(lines))


async def repl(agent: Agent) -> None:
    """Run the interactive REPL."""
    # Start session
    await agent.start_session()

    # Connect to MCP servers
    mcp_errors = await agent.connect_mcp_servers()
    for server, error in mcp_errors.items():
        if error:
            print_system(f"Warning: MCP server '{server}' connect failed: {error}")

    _print_banner(agent)

    history: list[dict] = []

    try:
        while True:
            try:
                if rich_enabled():
                    user_input = prompt_input_markup("[bold bright_green]You:[/] ").strip()
                else:
                    user_input = prompt_input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input or user_input.lower() in ("exit", "quit", "q"):
                break

            if user_input.startswith("/"):
                cmd = user_input.strip().split()[0].lower()
                if cmd in ("/exit", "/quit"):
                    break
                if cmd == "/help":
                    if rich_enabled():
                        from rich.table import Table

                        table = Table(title="Commands", show_header=True, header_style="bold bright_cyan")
                        table.add_column("Command", style="cyan", no_wrap=True)
                        table.add_column("Description", style="white")
                        table.add_row("/help", "Show this help")
                        table.add_row("/info", "Show session info")
                        table.add_row("/paste", "Multiline input (end with '.')")
                        table.add_row("/reset", "Clear conversation history")
                        table.add_row("/clear", "Clear the screen")
                        table.add_row("/exit", "Quit")
                        get_console().print(table)
                    else:
                        msg = "\n".join(
                            [
                                "/help   Show this help",
                                "/info   Show session info",
                                "/paste  Multiline input (end with '.')",
                                "/reset  Clear conversation history",
                                "/clear  Clear the screen",
                                "/exit   Quit",
                            ]
                        )
                        print_panel("Commands", msg)
                    continue
                if cmd == "/info":
                    _print_banner(agent)
                    continue
                if cmd == "/clear":
                    clear_screen()
                    _print_banner(agent)
                    continue
                if cmd == "/reset":
                    try:
                        ok = prompt_input("Reset conversation history? [y/N] ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        ok = ""
                    if ok in {"y", "yes"}:
                        history.clear()
                        print_system("Conversation history cleared.")
                    continue
                if cmd == "/paste":
                    user_input = _read_paste()
                    if not user_input:
                        continue
                else:
                    print_system("Unknown command. Try /help")
                    continue

            history.append({"role": "user", "content": user_input})

            try:
                await agent.run_loop(history)
            except Exception as e:
                print_system(f"Error: {e}")

            print()
    finally:
        # End session
        await agent.end_session()
        print_system("Session ended.")


def main() -> None:
    """Main entry point."""
    workdir = Path.cwd()
    _setup_readline(workdir / ".minibot" / "history")
    config = load_config(workdir=workdir)
    agent = Agent(config)

    asyncio.run(repl(agent))


if __name__ == "__main__":
    main()
