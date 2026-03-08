import os
from pathlib import Path
from typing import Any, Callable

from .commands_hitl import (
    run_allow_command,
    run_always_allow_command,
    run_deny_command,
    run_safe_command,
    run_stop_command,
    run_yolo_command,
)
from .nous import (
    append_nous_text,
    load_nous_text,
    reinforce_nous_from_dialogue,
    write_nous_text,
)
from .settings import load_runtime_settings
from .skills import DEFAULT_SKILLS_DIR, SUPPORTED_SKILL_EXTENSIONS

DEFAULT_TELEGRAM_COMMANDS = [
    {"command": "start", "description": "Start using Abraxas"},
    {"command": "help", "description": "Show help text"},
    {"command": "commands", "description": "List available commands"},
    {"command": "photos", "description": "send photo(s) by local path"},
    {"command": "compact", "description": "Compact current chat session"},
    {"command": "checkpoint", "description": "Approve or dismiss a checkpoint suggestion"},
    {"command": "handoff", "description": "Handoff with summary"},
    {"command": "new", "description": "Start a fresh session"},
    {"command": "memory", "description": "Query memory"},
    {"command": "remember", "description": "Save to memory"},
    {"command": "nous", "description": "Manage NOUS system prompt"},
    {"command": "tape", "description": "Show audit tape"},
    {"command": "rci", "description": "RCI mode control"},
    {"command": "yolo", "description": "Enable autonomous mode (no approval)"},
    {"command": "safe", "description": "Enable safe mode (approval required)"},
    {"command": "stop", "description": "Stop current execution"},
    {"command": "sync_commands", "description": "sync command menu"},
]

RefreshCallback = Callable[[Any | None], str]


def build_help_text(commands: list[dict[str, str]] | None = None) -> str:
    _ = commands
    lines = [
        "I am Abraxas.",
        "Talk to me in normal language: goals, bugs, architecture, or raw ideas.",
        "I will reason, run tools when needed, and answer directly.",
        "Use /commands to inspect command menu, built-in tools, plugins, and skills.",
        "Use /photos <local_path>[,<local_path2>] to send existing local images.",
        "Use /photos with no path to resend recent generated images.",
        "Use /checkpoint yes|no|show to manage a proposed phase boundary.",
        "Use /handoff <summary> [#tag] [--pure] to write an explicit phase boundary.",
        "Use /tape status|tail|search|anchors|reset to inspect session tape.",
        "Use /rci on|off|status to control strict execution protocol mode.",
        "Use /safe, /yolo, /allow, /deny, /stop for execution control.",
        "Use /new to start a fresh conversation in this chat.",
        "Use /memory to inspect status or run memory/mission sync.",
    ]
    return "\n".join(lines)


def run_memory_command(bot: Any, raw_args: str) -> str:
    usage = (
        "memory usage:\n"
        "/memory status\n"
        "/memory doctor\n"
        "/memory sync\n"
        "/memory promote\n"
        "/memory compound\n"
        "/memory query <text>"
    )
    runtime = getattr(bot, "memory_runtime", None)
    if runtime is None:
        return "memory unavailable"

    raw = (raw_args or "").strip()
    if not raw:
        return usage
    parts = raw.split(maxsplit=1)
    action = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if action in {"help", "-h", "--help"}:
        return usage

    if action == "status":
        if hasattr(runtime, "memory_status"):
            return str(runtime.memory_status())
        qmd_status = runtime.qmd_status() if hasattr(runtime, "qmd_status") else "unknown"
        return f"memory status:\n- qmd_status: {qmd_status}"

    if action == "doctor":
        if hasattr(runtime, "doctor_report"):
            return str(runtime.doctor_report())
        if hasattr(runtime, "memory_status"):
            return str(runtime.memory_status())
        return "memory doctor unavailable"

    if action == "query":
        if not arg:
            return "memory error: usage /memory query <text>"
        if hasattr(runtime, "query"):
            out = runtime.query(arg)
            return out or "memory query returned empty result"
        return "memory query unavailable"

    if action == "promote":
        lines: list[str] = []
        if hasattr(runtime, "promote_braindump_to_mission"):
            lines.append(str(runtime.promote_braindump_to_mission()))
        if hasattr(runtime, "sync_mission_to_memory"):
            lines.append(str(runtime.sync_mission_to_memory()))
        if hasattr(runtime, "refresh_index"):
            lines.append(str(runtime.refresh_index()))
        return "\n".join(lines) if lines else "memory promote unavailable"

    if action == "compound":
        lines: list[str] = []
        if hasattr(runtime, "compound_weekly_memory"):
            lines.append(str(runtime.compound_weekly_memory()))
        if hasattr(runtime, "sync_mission_to_memory"):
            lines.append(str(runtime.sync_mission_to_memory()))
        if hasattr(runtime, "refresh_index"):
            lines.append(str(runtime.refresh_index()))
        return "\n".join(lines) if lines else "memory compound unavailable"

    if action == "sync":
        lines: list[str] = []
        if hasattr(bot, "flush_memory_snapshot"):
            lines.append(str(bot.flush_memory_snapshot(reason="manual-sync", refresh_index=False)))
        if hasattr(runtime, "promote_braindump_to_mission"):
            lines.append(str(runtime.promote_braindump_to_mission()))
        if hasattr(runtime, "sync_mission_to_memory"):
            lines.append(str(runtime.sync_mission_to_memory()))
        if hasattr(runtime, "refresh_index"):
            lines.append(str(runtime.refresh_index()))
        return "\n".join(lines) if lines else "memory sync unavailable"

    return f"memory error: unknown action: {action}"


def _resolve_skills_dir(skills_dir: str) -> Path:
    path = Path(skills_dir)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def list_recent_photo_paths(limit: int = 3, search_dir: str = "outputs/images") -> list[str]:
    root = Path(search_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    if not root.exists() or not root.is_dir():
        return []
    suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    files = [
        item for item in root.iterdir() if item.is_file() and item.suffix.lower() in suffixes
    ]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return [str(item.resolve()) for item in files[:limit]]


def resolve_recent_photo_paths(
    raw_args: str,
    *,
    search_dir: str = "outputs/images",
) -> tuple[list[str], str | None]:
    raw = (raw_args or "").strip()
    if not raw:
        paths = list_recent_photo_paths(limit=3, search_dir=search_dir)
        if not paths:
            return [], f"no photos found in {Path(search_dir).as_posix()}"
        return paths, None

    if raw in {"help", "-h", "--help"}:
        return [], "photos usage: /photos <local_path>[,<local_path2>]"

    raw_items = [part.strip() for part in raw.split(",") if part.strip()]
    if not raw_items:
        return [], "photos error: usage /photos <local_path>[,<local_path2>]"

    paths: list[str] = []
    missing: list[str] = []
    for item in raw_items:
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if path.exists() and path.is_file():
            paths.append(str(path))
        else:
            missing.append(item)

    if missing:
        return [], "photos error: path not found: " + ", ".join(missing)
    if not paths:
        return [], "photos error: no valid local photo path provided"
    return paths, None


def run_photos_command(raw_args: str, *, search_dir: str = "outputs/images") -> str:
    paths, error = resolve_recent_photo_paths(raw_args, search_dir=search_dir)
    if error:
        return error
    return "recent photos:\n" + "\n".join(f"- {path}" for path in paths)


def list_skill_files(skills_dir: str | None = None) -> list[str]:
    runtime = load_runtime_settings()
    resolved = _resolve_skills_dir(skills_dir or str(runtime.get("skills_dir", DEFAULT_SKILLS_DIR)))
    if not resolved.exists() or not resolved.is_dir():
        return []
    return sorted(
        path.name
        for path in resolved.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SKILL_EXTENSIONS
    )


def collect_tool_inventory(bot: Any | None) -> tuple[list[str], list[str]]:
    runtime = getattr(bot, "tool_registry", None) if bot is not None else None
    if runtime is None or not hasattr(runtime, "tool_specs"):
        return [], []
    try:
        specs = runtime.tool_specs()
    except Exception:
        return [], []

    builtins: list[str] = []
    plugins: list[str] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        fn = spec.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", "")).strip()
        if not name:
            continue
        desc = str(fn.get("description", "")).strip().lower()
        if desc.startswith("[builtin]"):
            builtins.append(name)
        elif desc.startswith("[plugin]"):
            plugins.append(name)
        else:
            plugins.append(name)
    return sorted(set(builtins)), sorted(set(plugins))


def _collect_tool_inventory_with_mcp(bot: Any | None) -> tuple[list[str], list[str], list[str]]:
    builtin, plugin = collect_tool_inventory(bot)
    runtime = getattr(bot, "tool_registry", None) if bot is not None else None
    if runtime is None or not hasattr(runtime, "tool_specs"):
        return builtin, plugin, []
    try:
        specs = runtime.tool_specs()
    except Exception:
        return builtin, plugin, []
    mcp: list[str] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        fn = spec.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", "")).strip()
        if name.startswith("mcp_"):
            mcp.append(name)
    plugin = [name for name in plugin if not name.startswith("mcp_")]
    return builtin, sorted(set(plugin)), sorted(set(mcp))


def build_commands_text(
    bot: Any | None = None,
    skills_dir: str | None = None,
    commands: list[dict[str, str]] | None = None,
) -> str:
    builtins, plugins, mcp_tools = _collect_tool_inventory_with_mcp(bot)
    skill_files = list_skill_files(skills_dir)
    command_defs = commands or DEFAULT_TELEGRAM_COMMANDS
    command_names = [f"/{item['command']}" for item in command_defs if item.get("command")]
    builtin_text = ", ".join(builtins) if builtins else "(none)"
    plugin_text = ", ".join(plugins) if plugins else "(none)"
    mcp_text = ", ".join(mcp_tools) if mcp_tools else "(none)"
    skills_text = ", ".join(skill_files) if skill_files else "(none)"
    commands_text = ", ".join(command_names) if command_names else "(none)"
    lines = [
        "Capabilities",
        f"commands: {commands_text}",
        f"builtin tools: {builtin_text}",
        f"plugin tools: {plugin_text}",
        f"mcp tools: {mcp_text}",
        f"skills: {skills_text}",
    ]
    return "\n".join(lines)


def _default_refresh_callback(bot: Any | None) -> str:
    if bot is not None and hasattr(bot, "refresh_system_prompt"):
        return str(bot.refresh_system_prompt())
    return "skipped"


def run_nous_command(
    bot: Any | None,
    raw_args: str,
    *,
    refresh_callback: RefreshCallback | None = None,
) -> str:
    raw = (raw_args or "").strip()
    if not raw or raw == "show":
        text_out = load_nous_text()
        return text_out or "NOUS is empty."

    refresh = refresh_callback or _default_refresh_callback

    if raw.startswith("set "):
        body = raw[len("set ") :].strip()
        if not body:
            return "nous error: usage /nous set <text>"
        try:
            path = write_nous_text(body)
        except Exception as exc:
            return f"nous error: {exc}"
        return f"NOUS updated at {path}. {refresh(bot)}"

    if raw.startswith("append "):
        body = raw[len("append ") :].strip()
        if not body:
            return "nous error: usage /nous append <text>"
        try:
            path = append_nous_text(body)
        except Exception as exc:
            return f"nous error: {exc}"
        return f"NOUS appended at {path}. {refresh(bot)}"

    if raw.startswith("habit "):
        body = raw[len("habit ") :].strip()
        if not body:
            return "nous error: usage /nous habit <text>"
        try:
            path, section = reinforce_nous_from_dialogue(body, force_habit=True)
        except Exception as exc:
            return f"nous error: {exc}"
        return f"NOUS reinforced in {section} at {path}. {refresh(bot)}"

    try:
        path, section = reinforce_nous_from_dialogue(raw)
    except Exception as exc:
        return f"nous error: {exc}"
    return f"NOUS reinforced in {section} at {path}. {refresh(bot)}"


def run_compact_command(bot: Any, raw_args: str) -> str:
    keep_last_messages = 12
    instructions: str | None = None
    raw = (raw_args or "").strip()
    if raw:
        parts = raw.split(maxsplit=1)
        first = parts[0].strip()
        if first.isdigit():
            keep_last_messages = int(first)
            if keep_last_messages <= 0:
                return "compact error: usage /compact [positive_integer] [instructions]"
            if len(parts) > 1:
                instructions = parts[1].strip() or None
        else:
            instructions = raw

    if hasattr(bot, "compact_session"):
        return str(
            bot.compact_session(
                keep_last_messages=keep_last_messages,
                instructions=instructions,
            )
        )
    return "compact unavailable"


def run_checkpoint_command(bot: Any, raw_args: str) -> str:
    if bot is None:
        return "checkpoint unavailable"
    action = str(raw_args or "").strip().lower()
    if action in {"", "show", "status"}:
        if hasattr(bot, "show_checkpoint_proposal"):
            return str(bot.show_checkpoint_proposal())
        return "checkpoint unavailable"
    if action in {"yes", "accept", "approve"}:
        if hasattr(bot, "approve_checkpoint_proposal"):
            return str(bot.approve_checkpoint_proposal())
        return "checkpoint unavailable"
    if action in {"no", "reject", "dismiss"}:
        if hasattr(bot, "reject_checkpoint_proposal"):
            return str(bot.reject_checkpoint_proposal())
        return "checkpoint unavailable"
    return "usage: /checkpoint [yes|no|show]"


def run_handoff_command(bot: Any, raw_args: str) -> str:
    if bot is None:
        return "handoff unavailable"
    if not hasattr(bot, "handoff_session") and not hasattr(bot, "compact_or_handoff"):
        return "handoff unavailable"
    parts = str(raw_args or "").strip().split()
    if not parts:
        return "usage: /handoff <summary> [#tag1 #tag2 ...] [--pure]"
    pure = any(part == "--pure" for part in parts)
    tags = [part.lstrip("#") for part in parts if part.startswith("#")]
    summary = " ".join(part for part in parts if not part.startswith("#") and part != "--pure").strip()
    if not summary:
        return "usage: /handoff <summary> [#tag1 #tag2 ...] [--pure]"
    if pure or not hasattr(bot, "compact_or_handoff"):
        return str(bot.handoff_session(summary, tags=tags if tags else None))
    return str(bot.compact_or_handoff(summary, tags=tags if tags else None))


def run_new_session_command(bot: Any) -> str:
    if hasattr(bot, "start_new_session"):
        return str(bot.start_new_session())
    return "new session unavailable"


def run_rci_command(bot: Any, raw_args: str) -> str:
    if bot is None:
        return "rci unavailable"
    args = str(raw_args or "").strip().lower()
    if not args or args == "status":
        if hasattr(bot, "get_rci_status"):
            return str(bot.get_rci_status())
        return "rci unavailable"
    if args == "on" or args.startswith("on "):
        parts = args.split()
        minutes = 30
        if len(parts) > 1 and parts[1].isdigit():
            minutes = int(parts[1])
        if hasattr(bot, "enable_strict_rci_mode"):
            return str(bot.enable_strict_rci_mode(duration_minutes=minutes))
        return "rci unavailable"
    if args in {"off", "disable"}:
        if hasattr(bot, "disable_strict_rci_mode"):
            return str(bot.disable_strict_rci_mode())
        return "rci unavailable"
    return "usage: /rci [on [minutes]|off|status]"


def run_tape_command(bot: Any, raw_args: str) -> str:
    if bot is None:
        return "tape unavailable"
    tape = getattr(bot, "tape", None)
    if tape is None:
        return "tape engine not initialized"
    args = str(raw_args or "").strip().split()
    if not args:
        args = ["status"]
    subcommand = args[0].lower()

    if subcommand == "status":
        try:
            stats = tape.stats()
        except Exception as exc:
            return f"tape status error: {exc}"
        lines = [
            "Audit Tape Status",
            f"session_id: {stats.get('session_id', 'unknown')}",
            f"current_file: {stats.get('current_file', 'N/A')}",
            f"current_entries: {stats.get('current_entries', 0)}",
            f"current_size_bytes: {stats.get('current_size_bytes', 0)}",
            f"total_files: {stats.get('total_files', 0)}",
            f"total_size_bytes: {stats.get('total_size_bytes', 0)}",
            f"tape_dir: {stats.get('tape_dir', 'N/A')}",
        ]
        return "\n".join(lines)

    if subcommand == "tail":
        n = 20
        if len(args) > 1 and args[1].isdigit():
            n = int(args[1])
        try:
            entries = tape.tail(n)
        except Exception as exc:
            return f"tape tail error: {exc}"
        if not entries:
            return "Tape is empty."
        lines = [f"Last {len(entries)} entries (most recent last):"]
        for index, entry in enumerate(entries, 1):
            ts = str(entry.get("ts", "??"))[:19]
            role = str(entry.get("role", "??"))
            content = str(entry.get("content", "") or "")[:100]
            name = str(entry.get("name", "") or "")
            if name:
                role = f"{role}:{name}"
            lines.append(f"{index}. [{ts}] {role}: {content}...")
        return "\n".join(lines)

    if subcommand == "search":
        if len(args) < 2:
            return "usage: /tape search <query>"
        query = " ".join(args[1:])
        try:
            matches = tape.search(query, limit=50)
        except Exception as exc:
            return f"tape search error: {exc}"
        if not matches:
            return f"No matches found for: {query}"
        lines = [f"Found {len(matches)} matches for: {query}"]
        for index, entry in enumerate(matches[:50], 1):
            ts = str(entry.get("ts", "??"))[:19]
            role = str(entry.get("role", "??"))
            content = str(entry.get("content", "") or "")[:80]
            name = str(entry.get("name", "") or "")
            if name:
                role = f"{role}:{name}"
            lines.append(f"{index}. [{ts}] {role}: {content}...")
        return "\n".join(lines)

    if subcommand == "anchors":
        if hasattr(bot, "list_tape_anchors"):
            anchors = bot.list_tape_anchors()
        else:
            anchors = []
        if not anchors:
            return "No anchors recorded."
        lines = ["Tape Anchors"]
        for index, anchor in enumerate(anchors, 1):
            lines.append(
                f"{index}. [{anchor.get('ts', '')[:19]}] {anchor.get('event', 'unknown')}: {anchor.get('summary', '')}"
            )
        return "\n".join(lines)

    if subcommand == "reset":
        archive = any(part.lower() == "archive=true" for part in args[1:])
        if hasattr(bot, "reset_tape"):
            return str(bot.reset_tape(archive=archive))
        return "tape reset unavailable"

    return "usage: /tape [status|tail [n]|search <text>|anchors|reset [archive=true]]"


def run_remember_command(bot: Any, raw_args: str) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        return "remember error: usage /remember <note>"
    tags = [part[1:] for part in raw.split() if part.startswith("#") and len(part) > 1]
    if hasattr(bot, "remember"):
        return str(bot.remember(raw, tags=tags))
    return "memory unavailable"
