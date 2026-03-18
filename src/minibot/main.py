"""Minibot application entry point."""

import asyncio
import atexit
from pathlib import Path

from .config import load_config
from .agent import Agent, UserInterruptedError
from .session.manager import SessionManager
from .utils.esc_interrupt import EscInterruptMonitor
from .utils.output import clear_screen, print_assistant, print_panel, print_system, prompt_input, prompt_input_markup
from .utils.rich_utils import rich_enabled, get_console
from .utils.prompt_toolkit import SlashCommand, PromptToolkitInput, prompt_toolkit_enabled
from .utils.path import resolve_app_home, resolve_project_root
from .ui.cmd_memory import handle_memory_cmd
from .ui.cmd_agent import handle_agent_cmd
from .ui.cmd_mcp import handle_mcp_cmd
from .ui.cmd_model import handle_model_cmd
from .ui.cmd_skills import handle_skills_cmd, load_skills_disabled
from .ui.cmd_session import handle_session_cmd


SLASH_COMMANDS: dict[str, str] = {
    "/help": "Show this help",
    "/info": "Show session info",
    "/stream": "Streaming control: /stream [on|off|status]",
    "/memory": "Memory management: /memory [show|edit|list|daily|append]",
    "/skills": "Skills toggle: /skills [list|enable|disable|toggle]",
    "/agent": "Subagent management: /agent [list|info|enable|disable|create|delete]",
    "/mcp": "MCP server management: /mcp [list]",
    "/model": "Model management: /model [config]",
    "/session": "Session management: /session [list|new|load|delete|info]",
    "/paste": "Multiline input (end with '.')",
    "/reset": "Clear conversation history",
    "/clear": "Clear the screen",
    "/exit": "Quit",
    "/quit": "Quit",
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
            matches = sorted([c for c in SLASH_COMMANDS.keys() if c.startswith(text)])
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


def _print_banner(agent: Agent) -> None:
    info = agent.get_info()
    lines = [
        f"workdir: {info['workdir']}",
        f"model: {info['model']}  session: {info['session_id']}",
        f"role: {info.get('role', 'solo')}",
        f"skills: {', '.join(info['skills']) or 'none'}",
        f"agent types: {', '.join(info['agent_types'])}",
    ]
    stream = info.get("stream", {})
    if isinstance(stream, dict):
        lines.append(
            f"stream: mode={stream.get('mode', 'off')} active={stream.get('active', False)}"
        )
    if info.get("mcp_servers"):
        lines.append(f"mcp: {', '.join(info['mcp_servers'])}  tools: {info['mcp_tools']}")
    team = info.get("team", {})
    if isinstance(team, dict) and team.get("active"):
        lines.append(
            f"team: {team.get('name')} ({team.get('team_id')}) "
            f"members={team.get('member_count')} running={team.get('running_members')}"
        )
    lines.append("")
    lines.append("Tips: /help  /stream  /paste  /reset  /clear  exit  (ESC: interrupt generation)")
    print_panel("MiniBot", "\n".join(lines))


def _render_history(history: list[dict]) -> None:
    """Render loaded session history to the terminal."""
    if not history:
        return
    print_system("── session history ──")
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            if rich_enabled():
                get_console().print(f"[bold bright_green]You:[/] {content}")
            else:
                print(f"You: {content}")
        elif role == "assistant" and content:
            print_assistant(content)
    print_system("── end of history ──")
    print()


async def repl(agent: Agent) -> None:
    """Run the interactive REPL."""
    history: list[dict] = []
    session_mgr: SessionManager | None = None
    current_session_id = agent.session_id

    if agent.config.session.enabled:
        from pathlib import Path as _Path

        session_mgr = SessionManager(_Path(agent.config.session.sessions_dir))
        current_session_id = session_mgr.create()
        agent.reset_session(current_session_id)
    else:
        session_mgr = None

    # Start session
    await agent.start_session()

    # Connect to MCP servers
    mcp_errors = await agent.connect_mcp_servers()
    for server, error in mcp_errors.items():
        if error:
            print_system(f"Warning: MCP server '{server}' connect failed: {error}")

    _print_banner(agent)
    pt_input: PromptToolkitInput | None = None
    if prompt_toolkit_enabled():
        app_home = resolve_app_home()
        pt_input = PromptToolkitInput(
            history_path=app_home / "history" / "prompt_history",
            commands=[SlashCommand(name=k, description=v) for k, v in SLASH_COMMANDS.items()],
        )

    async def _prompt(*, rich_label: str, plain_label: str) -> str:
        if pt_input is not None:
            return await pt_input.prompt_async(label=plain_label)
        if rich_enabled():
            return prompt_input_markup(rich_label).strip()
        return prompt_input(plain_label).strip()

    async def _confirm(prompt: str) -> bool:
        if pt_input is not None:
            return await pt_input.confirm_async(prompt, default=False)
        try:
            ok = prompt_input(prompt + " [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ok = ""
        return ok in {"y", "yes"}

    async def _read_paste_mode() -> str:
        print_system("Paste mode: enter text, finish with a single '.' on its own line.")
        lines: list[str] = []
        while True:
            try:
                line = await _prompt(rich_label="… ", plain_label="… ")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() == ".":
                break
            lines.append(line)
        return "\n".join(lines).strip()

    try:
        while True:
            try:
                user_input = await _prompt(rich_label="[bold bright_green]You:[/] ", plain_label="You: ")
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
                        for name, desc in SLASH_COMMANDS.items():
                            if name == "/quit":
                                continue
                            table.add_row(name, desc)
                        get_console().print(table)
                    else:
                        msg = "\n".join(f"{k:<7} {v}" for k, v in SLASH_COMMANDS.items() if k != "/quit")
                        print_panel("Commands", msg)
                    print_system("Tip: press ESC while model is generating to interrupt.")
                    continue
                if cmd == "/info":
                    _print_banner(agent)
                    continue
                if cmd == "/stream":
                    args = user_input.strip().split()
                    action = args[1].lower() if len(args) > 1 else "status"
                    if action == "status":
                        stream = agent.get_stream_state()
                        print_system(
                            "Streaming: "
                            f"mode={stream['mode']} enabled={stream['enabled']} "
                            f"degraded={stream['degraded']} active={stream['active']}"
                        )
                    elif action == "on":
                        agent.set_stream_enabled(True)
                        stream = agent.get_stream_state()
                        print_system(f"Streaming enabled (mode={stream['mode']}).")
                    elif action == "off":
                        agent.set_stream_enabled(False)
                        stream = agent.get_stream_state()
                        print_system(f"Streaming disabled (mode={stream['mode']}).")
                    else:
                        print_system("Usage: /stream [on|off|status]")
                    continue
                if cmd == "/clear":
                    clear_screen()
                    _print_banner(agent)
                    continue
                if cmd == "/reset":
                    if await _confirm("Reset conversation history?"):
                        history.clear()
                        print_system("Conversation history cleared.")
                    continue
                if cmd == "/memory":
                    cmd_args = user_input.strip()[len("/memory"):].strip()
                    await handle_memory_cmd(cmd_args, agent.memory_manager)
                    continue
                if cmd == "/skills":
                    cmd_args = user_input.strip()[len("/skills"):].strip()
                    await handle_skills_cmd(cmd_args, agent)
                    continue
                if cmd == "/agent":
                    cmd_args = user_input.strip()[len("/agent"):].strip()
                    config_path = agent.config.app_home / "config" / "default.yaml"

                    async def _agent_prompt(rich_label: str, plain_label: str) -> str:
                        return await _prompt(rich_label=rich_label, plain_label=plain_label)

                    await handle_agent_cmd(
                        cmd_args, agent.agent_registry,
                        config_path, _agent_prompt,
                    )
                    continue
                if cmd == "/mcp":
                    cmd_args = user_input.strip()[len("/mcp"):].strip()
                    config_path = agent.config.app_home / "config" / "mcp_servers.yaml"
                    await handle_mcp_cmd(
                        cmd_args, agent.mcp_manager,
                        config_path, agent.tool_registry,
                    )
                    continue
                if cmd == "/model":
                    cmd_args = user_input.strip()[len("/model"):].strip()
                    app_home = agent.config.app_home

                    async def _model_prompt(rich_label: str, plain_label: str) -> str:
                        return await _prompt(rich_label=rich_label, plain_label=plain_label)

                    await handle_model_cmd(
                        cmd_args, app_home, _model_prompt,
                    )
                    continue
                if cmd == "/session":
                    cmd_args = user_input.strip()[len("/session"):].strip()
                    action = await handle_session_cmd(
                        cmd_args, session_mgr, current_session_id,
                    )
                    if action is not None and "switch_to" in action:
                        current_session_id = action["switch_to"]
                        history.clear()
                        history.extend(action.get("new_history", []))
                        agent.reset_session(current_session_id)
                        _print_banner(agent)
                        _render_history(history)
                    continue

                if cmd == "/paste":
                    user_input = await _read_paste_mode()
                    if not user_input:
                        continue
                else:
                    print_system("Unknown command. Try /help")
                    continue

            history.append({"role": "user", "content": user_input})
            if session_mgr is not None:
                session_mgr.append_message(
                    current_session_id, {"role": "user", "content": user_input},
                )

            pre_len = len(history)

            try:
                async with EscInterruptMonitor() as esc_monitor:
                    await agent.run_loop(history, interrupt_queue=esc_monitor.queue)
            except UserInterruptedError:
                print_system("Interrupted (ESC).")
                history.append({"role": "assistant", "content": "[Generation interrupted by user via ESC]"})
            except Exception as e:
                print_system(f"Error: {e}")

            # Persist newly added messages
            if session_mgr is not None:
                for msg in history[pre_len:]:
                    session_mgr.append_message(current_session_id, msg)

            print()
    finally:
        # End session
        await agent.end_session()
        print_system("Session ended.")


def main() -> None:
    """Main entry point."""
    workdir = Path.cwd().resolve()
    app_home = resolve_app_home()
    project_root = resolve_project_root(workdir)
    _setup_readline(app_home / "history" / "history")
    config = load_config(workdir=workdir, app_home=app_home, project_root=project_root)
    agent = Agent(config, disabled_skills=load_skills_disabled(app_home))

    asyncio.run(repl(agent))


if __name__ == "__main__":
    main()
