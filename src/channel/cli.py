import re

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from core.commands import (
    build_commands_text,
    build_help_text,
    run_compact_command,
    run_memory_command,
    run_nous_command,
    run_remember_command,
    run_tmux_plugin_command,
)
from core.bot import CodingBot
from core.registry import create_reloadable_tool_registry
from core.settings import load_runtime_settings

PROMPT_TEXT = "you> "


def render_reply(text: str) -> None:
    """Render reply with syntax highlighting for code blocks."""
    console = Console()
    parts = re.split(r"```(\w+)?\n(.*?)\n```", text, flags=re.DOTALL)
    if len(parts) == 1:
        console.print(make_reply_panel(text))
        return

    current_content: list[str] = []
    language = "text"
    for index, part in enumerate(parts):
        if index % 3 == 0:
            if part.strip():
                current_content.append(part)
            continue
        if index % 3 == 1:
            language = part or "text"
            continue
        if current_content:
            plain_text = "".join(current_content).strip()
            if plain_text:
                console.print(make_reply_panel(plain_text))
            current_content = []

        if part.strip():
            try:
                syntax = Syntax(part, language, theme="monokai", line_numbers=False)
                console.print(
                    Panel(
                        syntax,
                        title=f"code ({language})",
                        border_style="bright_blue",
                        box=box.ROUNDED,
                        padding=(1, 2),
                    )
                )
            except Exception:
                console.print(
                    Panel(
                        part,
                        title=f"code ({language})",
                        border_style="bright_blue",
                        box=box.ROUNDED,
                        padding=(1, 2),
                    )
                )

    if current_content:
        plain_text = "".join(current_content).strip()
        if plain_text:
            console.print(make_reply_panel(plain_text))


def make_reply_panel(text: str) -> Panel:
    return Panel(
        text,
        title="assistant",
        border_style="bright_green",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def make_input_session() -> PromptSession:
    return PromptSession(history=InMemoryHistory())


def handle_cli_command(text: str, bot: CodingBot) -> tuple[bool, str, bool]:
    command_text = text.strip()
    if not command_text:
        return False, "", False

    if command_text in {"/exit", "exit", "quit"}:
        return True, "bye.", True

    if command_text in {"/start", "/help"}:
        return True, build_help_text(), False

    if command_text.startswith("/commands"):
        return True, build_commands_text(bot=bot), False

    if command_text.startswith("/sync_commands"):
        return True, "sync_commands is Telegram-only.", False

    if command_text.startswith("/tmux"):
        raw_args = command_text[len("/tmux") :].strip()
        return True, run_tmux_plugin_command(bot, raw_args), False

    if command_text.startswith("/memory"):
        raw_args = command_text[len("/memory") :].strip()
        return True, run_memory_command(bot, raw_args), False

    if command_text.startswith("/compact"):
        raw_args = command_text[len("/compact") :].strip()
        return True, run_compact_command(bot, raw_args), False

    if command_text.startswith("/remember"):
        raw = command_text[len("/remember") :].strip()
        return True, run_remember_command(bot, raw), False

    if command_text.startswith("/nous"):
        raw = command_text[len("/nous") :].strip()
        return True, run_nous_command(bot, raw), False

    return False, "", False


def main() -> None:
    console = Console()
    config = load_runtime_settings()
    if not config["api_key"]:
        console.print("[red]Missing API key (API_KEY)[/red]")
        return

    tool_registry = create_reloadable_tool_registry()
    for plugin_error in tool_registry.drain_errors():
        console.print(f"[yellow]plugin warning[/yellow] {plugin_error}")

    bot = CodingBot(tool_registry=tool_registry)
    session = make_input_session()
    console.print(
        Panel(
            "[bold]Abraxas Coding Bot[/bold]\nUse /help for intro, /commands for capabilities, /exit to quit.",
            title="Ready",
            border_style="bright_cyan",
            box=box.DOUBLE,
            padding=(1, 2),
        )
    )

    while True:
        try:
            text = session.prompt(PROMPT_TEXT).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        handled, command_reply, should_exit = handle_cli_command(text, bot)
        if handled:
            if command_reply:
                render_reply(command_reply)
            if should_exit:
                break
            continue

        if text:
            reply = bot.ask(
                text, on_tool=lambda label: console.print(f"[yellow]tool[/yellow] {label}")
            )
            for plugin_error in tool_registry.drain_errors():
                console.print(f"[yellow]plugin warning[/yellow] {plugin_error}")
            render_reply(reply)


if __name__ == "__main__":
    main()
