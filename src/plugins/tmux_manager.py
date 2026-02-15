import shlex
import subprocess
from typing import Sequence

from core.tools import ToolPlugin

SESSION_NAME_MAX_LEN = 64
TMUX_HELP_TEXT = (
    "tmux usage:\n"
    "/tmux list\n"
    "/tmux new <session> [command...]\n"
    "/tmux send <session> <text>\n"
    "/tmux logs <session> [lines]\n"
    "/tmux kill <session>"
)


def _run_tmux(args: Sequence[str], timeout: int = 20) -> tuple[int, str, str]:
    command = ["tmux", *args]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "tmux not found"
    except Exception as exc:
        return 1, "", str(exc)


def _valid_session_name(name: str) -> bool:
    if not name or len(name) > SESSION_NAME_MAX_LEN:
        return False
    for ch in name:
        if ch.isalnum():
            continue
        if ch in {"_", "-", "."}:
            continue
        return False
    return True


def _list_sessions() -> str:
    code, stdout, stderr = _run_tmux(
        ["list-sessions", "-F", "#{session_name}\t#{session_attached}\t#{session_windows}"]
    )
    if code != 0:
        if "no server running" in stderr.lower():
            return "tmux sessions: (none)"
        if "tmux not found" in stderr.lower():
            return "tmux unavailable: tmux not installed"
        return f"tmux error: {stderr or 'list-sessions failed'}"
    if not stdout:
        return "tmux sessions: (none)"

    lines = ["tmux sessions:"]
    for raw_line in stdout.splitlines():
        name, attached, windows = (raw_line.split("\t") + ["?", "?"])[:3]
        lines.append(f"- {name} (attached={attached}, windows={windows})")
    return "\n".join(lines)


def _new_session(name: str, command_tokens: Sequence[str]) -> str:
    if not _valid_session_name(name):
        return "tmux error: invalid session name"
    args = ["new-session", "-d", "-s", name]
    if command_tokens:
        args.extend(command_tokens)
    code, _, stderr = _run_tmux(args)
    if code != 0:
        return f"tmux error: {stderr or 'new-session failed'}"
    return f"tmux created session: {name}"


def _send_to_session(name: str, text: str) -> str:
    if not _valid_session_name(name):
        return "tmux error: invalid session name"
    payload = text.strip()
    if not payload:
        return "tmux error: send text is empty"
    code, _, stderr = _run_tmux(["send-keys", "-t", name, payload, "C-m"])
    if code != 0:
        return f"tmux error: {stderr or 'send-keys failed'}"
    return f"tmux sent to {name}"


def _capture_logs(name: str, lines: int = 120) -> str:
    if not _valid_session_name(name):
        return "tmux error: invalid session name"
    if lines <= 0:
        lines = 120
    if lines > 2000:
        lines = 2000
    code, stdout, stderr = _run_tmux(["capture-pane", "-p", "-t", name, "-S", f"-{lines}"])
    if code != 0:
        return f"tmux error: {stderr or 'capture-pane failed'}"
    if not stdout:
        return f"tmux logs ({name}): (empty)"
    return f"tmux logs ({name}):\n{stdout}"


def _kill_session(name: str) -> str:
    if not _valid_session_name(name):
        return "tmux error: invalid session name"
    code, _, stderr = _run_tmux(["kill-session", "-t", name])
    if code != 0:
        return f"tmux error: {stderr or 'kill-session failed'}"
    return f"tmux killed session: {name}"


def _handle_command(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return TMUX_HELP_TEXT
    try:
        parts = shlex.split(raw)
    except Exception as exc:
        return f"tmux error: invalid arguments: {exc}"
    if not parts:
        return TMUX_HELP_TEXT

    action = parts[0].lower()
    args = parts[1:]

    if action in {"help", "-h", "--help"}:
        return TMUX_HELP_TEXT
    if action in {"list", "ls"}:
        return _list_sessions()
    if action == "new":
        if not args:
            return "tmux error: usage /tmux new <session> [command...]"
        return _new_session(args[0], args[1:])
    if action == "send":
        if len(args) < 2:
            return "tmux error: usage /tmux send <session> <text>"
        return _send_to_session(args[0], " ".join(args[1:]))
    if action == "logs":
        if not args:
            return "tmux error: usage /tmux logs <session> [lines]"
        lines = 120
        if len(args) > 1:
            try:
                lines = int(args[1])
            except ValueError:
                return "tmux error: lines must be an integer"
        return _capture_logs(args[0], lines=lines)
    if action == "kill":
        if not args:
            return "tmux error: usage /tmux kill <session>"
        return _kill_session(args[0])

    return f"tmux error: unknown action: {action}"


def _handle(payload: dict) -> str:
    command = str(payload.get("command", "")).strip()
    return _handle_command(command)


def register(registry) -> None:
    registry.register(
        ToolPlugin(
            name="tmux_manager",
            description="Manage tmux coding-agent sessions (list/new/send/logs/kill).",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
            handler=_handle,
        )
    )
