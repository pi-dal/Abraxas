import inspect
import re
from dataclasses import dataclass

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from core.commands import (
    build_commands_text,
    build_help_text,
    run_compact_command,
    run_checkpoint_command,
    run_handoff_command,
    run_memory_command,
    run_new_session_command,
    run_nous_command,
    run_photos_command,
    run_remember_command,
    run_rci_command,
    run_tape_command,
    run_yolo_command,
    run_safe_command,
    run_allow_command,
    run_always_allow_command,
    run_deny_command,
    run_stop_command,
)
from core.bot import CodingBot
from core.registry import create_reloadable_tool_registry
from core.settings import load_runtime_settings

PROMPT_TEXT = "you> "


@dataclass
class _CliInputSession:
    chat_id: str = "cli"
    thread_id: str = "cli"

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def prompt(self, text: str) -> str:
        return input(text)


def make_input_session() -> _CliInputSession:
    return _CliInputSession()


def make_reply_panel(text: str) -> Panel:
    return Panel(str(text), title="assistant", border_style="bright_green")


def _ask_bot_with_partial_support(bot: CodingBot, line: str, on_partial_response=None) -> str:
    ask = getattr(bot, "ask")
    try:
        signature = inspect.signature(ask)
    except (TypeError, ValueError):
        signature = None

    kwargs = {}
    if signature is not None:
        parameters = signature.parameters.values()
        accepts_var_kw = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        if accepts_var_kw or "on_partial_response" in signature.parameters:
            kwargs["on_partial_response"] = on_partial_response
        return ask(line, **kwargs)

    try:
        return ask(line, on_partial_response=on_partial_response)
    except TypeError:
        return ask(line)


def _stream_cli_reply(console: Console, bot: CodingBot, line: str) -> str:
    live: Live | None = None
    last_partial = ""

    def _on_partial_response(text: str) -> None:
        nonlocal last_partial
        content = str(text or "")
        if not content or content == last_partial or live is None:
            return
        last_partial = content
        live.update(make_reply_panel(content))

    with Live(make_reply_panel("..."), console=console, refresh_per_second=12, transient=True) as active_live:
        live = active_live
        return _ask_bot_with_partial_support(bot, line, on_partial_response=_on_partial_response)


def _parse_intercepted_message(content: str) -> dict[str, str] | None:
    """
    Parse an intercepted tool call message.
    Returns dict with 'tool_name' and 'parameters' if intercepted, else None.
    """
    if "[INTERCEPTED]" not in content:
        return None

    # Extract tool name
    tool_match = re.search(r"⚠️ Pending Tool Call: (\w+)", content)
    if not tool_match:
        return None

    tool_name = tool_match.group(1)

    # Extract parameters (everything after "Parameters:")
    params_match = re.search(r"Parameters: ({.*})", content, re.DOTALL)
    params_str = params_match.group(1) if params_match else "{}"

    return {
        "tool_name": tool_name,
        "parameters": params_str,
    }


def _render_intercepted_panel(tool_name: str, parameters: str, console: Console) -> None:
    """
    Render a rich panel for intercepted tool calls.
    """
    # Build rich text for the panel
    panel_content = Text()
    panel_content.append("⚠️ ", style="bold yellow")
    panel_content.append("Bot requested to run:\n\n", style="bold")
    panel_content.append(f"Tool: {tool_name}\n", style="bold cyan")
    panel_content.append(f"Parameters: {parameters}", style="dim")

    console.print(Panel(
        panel_content,
        title="[bold yellow]EXECUTION INTERCEPTED[/bold yellow]",
        border_style="red",
        padding=(1, 2),
    ))


def _handle_intercepted_interception(content: str, console: Console, bot: CodingBot) -> None:
    """
    Handle an intercepted tool call with interactive UI.

    Prints the outcome directly via console:
    - y  = Allow once (safe mode remains)
    - a  = Always Allow (switch to YOLO for this session)
    - n  = Deny
    """
    parsed = _parse_intercepted_message(content)
    if not parsed:
        return

    # Render the intercepted panel
    _render_intercepted_panel(parsed["tool_name"], parsed["parameters"], console)

    # Show choice legend
    console.print(
        "  [bold green]y[/bold green] — Allow once\n"
        "  [bold yellow]a[/bold yellow] — Always Allow (activates YOLO mode for this session)\n"
        "  [bold red]n[/bold red] — Deny",
        highlight=False,
    )

    action = Prompt.ask("Action", choices=["y", "a", "n"], default="n")

    if action == "y":
        llm_reply = run_allow_command(bot)
        console.print(Panel("✅ Tool executed.", border_style="green", padding=(0, 2)))
        if llm_reply and llm_reply.strip():
            console.print(make_reply_panel(llm_reply))

    elif action == "a":
        llm_reply = run_always_allow_command(bot)
        console.print(Panel(
            "⚡ Always Allow — YOLO mode active. All tools will run without approval for this session.",
            border_style="yellow",
            padding=(0, 2),
        ))
        if llm_reply and llm_reply.strip():
            console.print(make_reply_panel(llm_reply))

    else:  # n
        llm_reply = run_deny_command(bot)
        console.print(Panel("❌ Tool denied.", border_style="red", padding=(0, 2)))
        if llm_reply and llm_reply.strip():
            console.print(make_reply_panel(llm_reply))


def handle_cli_command(line: str, bot: CodingBot) -> tuple[bool, str, bool]:
    """Try to run built-in commands.

    Returns:
        (handled, response, should_exit)
    """
    if line in {"/exit", "/quit"}:
        return True, "", True

    if line == "/help":
        return True, build_help_text(), False

    if line == "/commands":
        return True, build_commands_text(bot), False

    if line == "/sync_commands":
        return True, "Telegram-only command. Use it in Telegram chat.", False

    if line == "/yolo":
        return True, run_yolo_command(bot), False

    if line == "/safe":
        return True, run_safe_command(bot), False

    if line == "/allow":
        return True, run_allow_command(bot), False

    if line == "/deny":
        return True, run_deny_command(bot), False

    if line == "/stop":
        return True, run_stop_command(bot), False

    if line == "/nous" or line.startswith("/nous "):
        raw_args = line[len("/nous") :].strip()
        return True, run_nous_command(bot, raw_args), False

    if line == "/memory" or line.startswith("/memory "):
        raw_args = line[len("/memory") :].strip()
        return True, run_memory_command(bot, raw_args), False

    if line == "/photos" or line.startswith("/photos "):
        raw_args = line[len("/photos") :].strip()
        return True, run_photos_command(raw_args), False

    if line == "/compact" or line.startswith("/compact "):
        raw_args = line[len("/compact") :].strip()
        return True, run_compact_command(bot, raw_args), False

    if line == "/checkpoint" or line.startswith("/checkpoint "):
        raw_args = line[len("/checkpoint") :].strip()
        return True, run_checkpoint_command(bot, raw_args), False

    if line == "/handoff" or line.startswith("/handoff "):
        raw_args = line[len("/handoff") :].strip()
        return True, run_handoff_command(bot, raw_args), False

    if line == "/tape" or line.startswith("/tape "):
        raw_args = line[len("/tape") :].strip()
        return True, run_tape_command(bot, raw_args), False

    if line == "/rci" or line.startswith("/rci "):
        raw_args = line[len("/rci") :].strip()
        return True, run_rci_command(bot, raw_args), False

    if line == "/new":
        return True, run_new_session_command(bot), False

    if line == "/remember" or line.startswith("/remember "):
        raw_args = line[len("/remember") :].strip()
        return True, run_remember_command(bot, raw_args), False

    if line.startswith("/"):
        return True, "unknown command. use /help.", False

    # Not a command
    return False, "", False


def main() -> None:
    console = Console()
    config = load_runtime_settings()
    if not config["api_key"]:
        console.print("[red]Missing API key (API_KEY)[/red]")
        return

    registry = create_reloadable_tool_registry()
    for plugin_error in registry.drain_errors():
        console.print(f"[yellow]plugin warning[/yellow] {plugin_error}")
    bot = CodingBot(tool_registry=registry)

    console.print(
        "[bold cyan]Abraxas[/bold cyan] — [dim]interactive CLI[/dim]\n"
        f"Model: {config['model']}\n"
        "Type /help for commands.\n"
    )

    while True:
        try:
            line = console.input(PROMPT_TEXT).strip()
            if not line:
                continue

            # Handle commands
            handled, response, should_exit = handle_cli_command(line, bot)
            if should_exit:
                console.print("[dim]Session ended.[/dim]")
                break

            if handled:
                if response:
                    console.print(response)
                continue

            # Normal conversation — bot.ask() handles user-message appending internally.
            # Do NOT manually append the user message here.
            reply = _stream_cli_reply(console, bot, line)
            for plugin_error in registry.drain_errors():
                console.print(f"[yellow]plugin warning[/yellow] {plugin_error}")

            # Check if this is an intercepted message (ask() returns str directly)
            if "[INTERCEPTED]" in reply:
                # Handle with interactive UI — prints status badge + LLM reply directly
                _handle_intercepted_interception(reply, console, bot)
                # Continue to next iteration — allow/deny already drove the follow-up LLM turn
                continue

            console.print(make_reply_panel(reply))

        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted. Type /exit to quit.[/dim]")
            continue
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            continue


if __name__ == "__main__":
    main()
