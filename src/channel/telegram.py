import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.bot import CodingBot
from core.nous import (
    append_nous_text,
    load_nous_text,
    reinforce_nous_from_dialogue,
    write_nous_text,
)
from core.registry import ReloadableToolRegistry, create_reloadable_tool_registry
from core.scheduler import DailyScheduler
from core.skills import DEFAULT_SKILLS_DIR, SUPPORTED_SKILL_EXTENSIONS
from core.settings import load_settings, load_telegram_settings

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TELEGRAM_COMMANDS = [
    {"command": "help", "description": "intro and how to chat"},
    {"command": "commands", "description": "show commands, tools, plugins, skills"},
    {"command": "compact", "description": "compact current chat session"},
    {"command": "remember", "description": "save a note to memory"},
    {"command": "nous", "description": "show or reinforce NOUS profile"},
    {"command": "sync_commands", "description": "sync command menu"},
]


def build_help_text(commands: list[dict[str, str]] | None = None) -> str:
    _ = commands
    lines = [
        "I am Abraxas.",
        "Talk to me in normal language: goals, bugs, architecture, or raw ideas.",
        "I will reason, run tools when needed, and answer directly.",
        "Use /commands to inspect command menu, built-in tools, plugins, and skills.",
    ]
    return "\n".join(lines)


def _resolve_skills_dir(skills_dir: str) -> Path:
    path = Path(skills_dir)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def list_skill_files(skills_dir: str | None = None) -> list[str]:
    resolved = _resolve_skills_dir(skills_dir or os.getenv("ABRAXAS_SKILLS_DIR", DEFAULT_SKILLS_DIR))
    if not resolved.exists() or not resolved.is_dir():
        return []
    return sorted(
        path.name
        for path in resolved.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SKILL_EXTENSIONS
    )


def collect_tool_inventory(bot: CodingBot | None) -> tuple[list[str], list[str]]:
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


def build_commands_text(bot: CodingBot | None = None, skills_dir: str | None = None) -> str:
    builtins, plugins = collect_tool_inventory(bot)
    skill_files = list_skill_files(skills_dir)
    command_names = [f"/{item['command']}" for item in DEFAULT_TELEGRAM_COMMANDS if item.get("command")]
    builtin_text = ", ".join(builtins) if builtins else "(none)"
    plugin_text = ", ".join(plugins) if plugins else "(none)"
    skills_text = ", ".join(skill_files) if skill_files else "(none)"
    commands_text = ", ".join(command_names) if command_names else "(none)"
    lines = [
        "Capabilities",
        f"commands: {commands_text}",
        f"builtin tools: {builtin_text}",
        f"plugin tools: {plugin_text}",
        f"skills: {skills_text}",
    ]
    return "\n".join(lines)


class TelegramClient:
    def __init__(self, token: str, request_timeout: int = 35):
        if not token:
            raise ValueError("telegram token is required")
        self.base_url = f"{TELEGRAM_API_BASE}/bot{token}"
        self.request_timeout = request_timeout

    def _post(self, method: str, payload: dict) -> Any:
        req = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram request failed: {exc}") from exc
        data = json.loads(body)
        if not data.get("ok"):
            raise RuntimeError(f"telegram api error: {data.get('description', 'unknown')}")
        return data.get("result", {})

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._post("getUpdates", payload)
        if isinstance(result, list):
            return result
        return []

    def send_message(
        self, chat_id: int, text: str, reply_to_message_id: int | None = None
    ) -> dict:
        payload: dict[str, int | str] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        result = self._post("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        payload_commands: list[dict[str, str]] = []
        for command_item in commands:
            command = str(command_item.get("command", "")).strip()
            description = str(command_item.get("description", "")).strip()
            if not command:
                continue
            payload_commands.append({"command": command, "description": description[:256]})
        if not payload_commands:
            return False
        result = self._post("setMyCommands", {"commands": payload_commands})
        return bool(result)


def sync_telegram_commands(
    client: TelegramClient,
    commands: list[dict[str, str]] | None = None,
) -> bool:
    try:
        return client.set_my_commands(commands or DEFAULT_TELEGRAM_COMMANDS)
    except Exception:
        return False


def extract_message_payload(update: dict) -> tuple[int, int, str] | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    text = message.get("text")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    message_id = message.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int) or not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    return chat_id, message_id, text


def parse_allowed_chat_ids(raw_value: str) -> set[int]:
    values = [part.strip() for part in raw_value.split(",")]
    values = [part for part in values if part]
    if not values:
        raise ValueError("no allowed chat ids configured")

    allowed: set[int] = set()
    for value in values:
        try:
            allowed.add(int(value))
        except ValueError as exc:
            raise ValueError(f"invalid chat id: {value}") from exc
    return allowed


def chunk_message(text: str, limit: int = 4096) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = text
    while len(current) > limit:
        split_at = current.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(current[:split_at])
        current = current[split_at:].lstrip("\n")
    if current:
        chunks.append(current)
    return chunks


def process_update(
    update: dict,
    sessions: dict[int, CodingBot],
    client: TelegramClient,
    bot_factory,
    allowed_chat_ids: set[int] | None,
) -> None:
    payload = extract_message_payload(update)
    if payload is None:
        return
    chat_id, message_id, text = payload
    if allowed_chat_ids is not None and chat_id not in allowed_chat_ids:
        return

    def _refresh_system_prompts() -> int:
        refreshed = 0
        for bot in sessions.values():
            if hasattr(bot, "refresh_system_prompt"):
                try:
                    bot.refresh_system_prompt()
                    refreshed += 1
                except Exception:
                    pass
        return refreshed

    if text in {"/start", "/help"}:
        client.send_message(
            chat_id,
            build_help_text(),
            reply_to_message_id=message_id,
        )
        return

    if text.startswith("/commands"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot
        out = build_commands_text(bot=bot)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            client.send_message(
                chat_id,
                chunk,
                reply_to_message_id=message_id if index == 0 else None,
            )
        return

    if text.startswith("/nous"):
        raw = text[len("/nous") :].strip()
        if not raw or raw == "show":
            nous_text = load_nous_text()
            out = nous_text or "NOUS is empty."
            chunks = chunk_message(out)
            for index, chunk in enumerate(chunks):
                client.send_message(
                    chat_id,
                    chunk,
                    reply_to_message_id=message_id if index == 0 else None,
                )
            return

        if raw.startswith("set "):
            body = raw[len("set ") :].strip()
            if not body:
                client.send_message(
                    chat_id,
                    "nous error: usage /nous set <text>",
                    reply_to_message_id=message_id,
                )
                return
            try:
                path = write_nous_text(body)
            except Exception as exc:
                client.send_message(
                    chat_id,
                    f"nous error: {exc}",
                    reply_to_message_id=message_id,
                )
                return
            refreshed = _refresh_system_prompts()
            client.send_message(
                chat_id,
                f"NOUS updated at {path}. refreshed sessions: {refreshed}",
                reply_to_message_id=message_id,
            )
            return

        if raw.startswith("append "):
            body = raw[len("append ") :].strip()
            if not body:
                client.send_message(
                    chat_id,
                    "nous error: usage /nous append <text>",
                    reply_to_message_id=message_id,
                )
                return
            try:
                path = append_nous_text(body)
            except Exception as exc:
                client.send_message(
                    chat_id,
                    f"nous error: {exc}",
                    reply_to_message_id=message_id,
                )
                return
            refreshed = _refresh_system_prompts()
            client.send_message(
                chat_id,
                f"NOUS appended at {path}. refreshed sessions: {refreshed}",
                reply_to_message_id=message_id,
            )
            return

        if raw.startswith("habit "):
            body = raw[len("habit ") :].strip()
            if not body:
                client.send_message(
                    chat_id,
                    "nous error: usage /nous habit <text>",
                    reply_to_message_id=message_id,
                )
                return
            try:
                path, section = reinforce_nous_from_dialogue(body, force_habit=True)
            except Exception as exc:
                client.send_message(
                    chat_id,
                    f"nous error: {exc}",
                    reply_to_message_id=message_id,
                )
                return
            refreshed = _refresh_system_prompts()
            client.send_message(
                chat_id,
                f"NOUS reinforced in {section} at {path}. refreshed sessions: {refreshed}",
                reply_to_message_id=message_id,
            )
            return

        try:
            path, section = reinforce_nous_from_dialogue(raw)
        except Exception as exc:
            client.send_message(
                chat_id,
                f"nous error: {exc}",
                reply_to_message_id=message_id,
            )
            return
        refreshed = _refresh_system_prompts()
        client.send_message(
            chat_id,
            f"NOUS reinforced in {section} at {path}. refreshed sessions: {refreshed}",
            reply_to_message_id=message_id,
        )
        return

    if text.startswith("/sync_commands"):
        ok = sync_telegram_commands(client)
        sync_text = (
            "command menu synced with Telegram."
            if ok
            else "command menu sync failed. check token and bot permissions."
        )
        client.send_message(
            chat_id,
            sync_text,
            reply_to_message_id=message_id,
        )
        return

    if text.startswith("/remember"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot

        raw = text[len("/remember") :].strip()
        if not raw:
            client.send_message(
                chat_id,
                "remember error: usage /remember <note>",
                reply_to_message_id=message_id,
            )
            return

        tags = [part[1:] for part in raw.split() if part.startswith("#") and len(part) > 1]
        result = (
            bot.remember(raw, tags=tags)
            if hasattr(bot, "remember")
            else "memory unavailable"
        )
        client.send_message(
            chat_id,
            result,
            reply_to_message_id=message_id,
        )
        return

    if text.startswith("/compact"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot

        keep_last_messages = 12
        instructions: str | None = None
        raw_args = text[len("/compact") :].strip()
        if raw_args:
            parts = raw_args.split(maxsplit=1)
            first = parts[0].strip()
            if first.isdigit():
                keep_last_messages = int(first)
                if keep_last_messages <= 0:
                    client.send_message(
                        chat_id,
                        "compact error: usage /compact [positive_integer] [instructions]",
                        reply_to_message_id=message_id,
                    )
                    return
                if len(parts) > 1:
                    instructions = parts[1].strip() or None
            else:
                instructions = raw_args

        if hasattr(bot, "compact_session"):
            compact_result = bot.compact_session(
                keep_last_messages=keep_last_messages,
                instructions=instructions,
            )
        else:
            sessions[chat_id] = bot_factory()
            compact_result = "session compacted: session restarted."

        client.send_message(
            chat_id,
            compact_result,
            reply_to_message_id=message_id,
        )
        return

    bot = sessions.get(chat_id)
    if bot is None:
        bot = bot_factory()
        sessions[chat_id] = bot

    try:
        reply = bot.ask(text)
    except Exception as exc:
        reply = f"bot error: {exc}"

    chunks = chunk_message(reply or "(empty response)")
    for index, chunk in enumerate(chunks):
        client.send_message(
            chat_id,
            chunk,
            reply_to_message_id=message_id if index == 0 else None,
        )


def run_daily_memory_sync(sessions: dict[int, CodingBot]) -> dict[str, int]:
    synced = 0
    refreshed_indexes = 0
    seen_runtime_ids: set[int] = set()
    runtimes: list[Any] = []

    for bot in sessions.values():
        if hasattr(bot, "flush_memory_snapshot"):
            try:
                bot.flush_memory_snapshot(reason="daily-sync", refresh_index=False)
                synced += 1
            except Exception:
                pass
        runtime = getattr(bot, "memory_runtime", None)
        if runtime is not None and id(runtime) not in seen_runtime_ids:
            seen_runtime_ids.add(id(runtime))
            runtimes.append(runtime)

    for runtime in runtimes:
        try:
            runtime.refresh_index()
            refreshed_indexes += 1
        except Exception:
            pass

    return {"synced_sessions": synced, "refreshed_indexes": refreshed_indexes}


def run_telegram_bot(
    token: str,
    allowed_chat_ids: set[int] | None = None,
    poll_timeout: int = 25,
    idle_sleep: float = 0.2,
    bot_factory=CodingBot,
    tool_registry: ReloadableToolRegistry | None = None,
    sync_commands_on_start: bool = True,
) -> None:
    client = TelegramClient(token)
    if sync_commands_on_start:
        if sync_telegram_commands(client):
            print("telegram commands synced.")
        else:
            print("warning: telegram commands sync failed.")
    sessions: dict[int, CodingBot] = {}
    scheduler = DailyScheduler(
        time_text=os.getenv("ABRAXAS_MEMORY_DAILY_SYNC_TIME", "02:00"),
        tz_name=os.getenv("ABRAXAS_MEMORY_TZ", "Asia/Shanghai"),
    )
    offset: int | None = None

    while True:
        updates = client.get_updates(offset=offset, timeout=poll_timeout)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            process_update(update, sessions, client, bot_factory, allowed_chat_ids)
            if tool_registry is not None:
                for plugin_error in tool_registry.drain_errors():
                    print(f"plugin warning: {plugin_error}")
        if scheduler.run_if_due(lambda: run_daily_memory_sync(sessions)):
            print("daily memory sync executed.")
        if not updates:
            time.sleep(idle_sleep)


def main() -> None:
    telegram_config = load_telegram_settings()
    token = telegram_config["telegram_bot_token"]
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN")
        return

    config = load_settings()
    if not config["api_key"]:
        print("Missing API_KEY")
        return

    raw_allowed = telegram_config["allowed_telegram_chat_ids"] or ""
    allowed_chat_ids: set[int] | None = None
    if raw_allowed.strip():
        try:
            allowed_chat_ids = parse_allowed_chat_ids(raw_allowed)
        except ValueError as exc:
            print(f"Invalid ALLOWED_TELEGRAM_CHAT_IDS: {exc}")
            return

    tool_registry = create_reloadable_tool_registry()
    for plugin_error in tool_registry.drain_errors():
        print(f"plugin warning: {plugin_error}")

    print("Starting Telegram bot polling...")
    run_telegram_bot(
        token,
        allowed_chat_ids=allowed_chat_ids,
        bot_factory=lambda: CodingBot(tool_registry=tool_registry),
        tool_registry=tool_registry,
    )


if __name__ == "__main__":
    main()
