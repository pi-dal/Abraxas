import base64
import json
from pathlib import Path
from typing import Any

from core.bot import CodingBot
from core.commands import (
    build_commands_text,
    build_help_text,
    run_compact_command,
    run_memory_command,
    run_new_session_command,
    run_nous_command,
    run_remember_command,
    run_tmux_plugin_command,
)

from .telegram_client import TelegramClient, sync_telegram_commands


def extract_message_payload(update: dict) -> tuple[int, int, str] | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    text_value = message.get("text")
    if not isinstance(text_value, str):
        text_value = message.get("caption")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    message_id = message.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return None
    has_photo = bool(extract_largest_photo_file_id(message))
    text = text_value.strip() if isinstance(text_value, str) else ""
    if not text and not has_photo:
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


def extract_largest_photo_file_id(message: dict) -> str | None:
    photos = message.get("photo")
    if not isinstance(photos, list):
        return None
    best_file_id: str | None = None
    best_size = -1
    for item in photos:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id", "")).strip()
        if not file_id:
            continue
        raw_size = item.get("file_size", 0)
        try:
            size = int(raw_size)
        except Exception:
            size = 0
        if size >= best_size:
            best_size = size
            best_file_id = file_id
    return best_file_id


def extract_image_saved_paths(text: str) -> list[str]:
    result: list[str] = []
    if not text:
        return result
    marker = "image_saved:"
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line.startswith(marker):
            continue
        path_text = line[len(marker) :].strip()
        if path_text:
            result.append(path_text)
    return result


def send_generated_images_from_paths(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    image_paths: list[str],
) -> None:
    seen: set[str] = set()
    for image_path in image_paths:
        if image_path in seen:
            continue
        seen.add(image_path)
        resolved = Path(image_path).expanduser()
        if not resolved.exists() or not resolved.is_file():
            client.send_message(
                chat_id,
                f"image send skipped: file not found: {resolved}",
                reply_to_message_id=message_id,
            )
            continue
        try:
            client.send_photo(
                chat_id,
                str(resolved),
                caption=f"generated image\nsource: {resolved}",
                reply_to_message_id=message_id,
            )
        except Exception as exc:
            client.send_message(
                chat_id,
                f"image send error: {exc}",
                reply_to_message_id=message_id,
            )


def process_update(
    update: dict,
    sessions: dict[int, CodingBot],
    client: TelegramClient,
    bot_factory,
    allowed_chat_ids: set[int] | None,
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
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
        out = run_nous_command(
            None,
            raw,
            refresh_callback=lambda _bot: f"refreshed sessions: {_refresh_system_prompts()}",
        )
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            client.send_message(
                chat_id,
                chunk,
                reply_to_message_id=message_id if index == 0 else None,
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

    if text.startswith("/tmux"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot
        raw = text[len("/tmux") :].strip()
        out = run_tmux_plugin_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            client.send_message(
                chat_id,
                chunk,
                reply_to_message_id=message_id if index == 0 else None,
            )
        return

    if text.startswith("/memory"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot
        raw = text[len("/memory") :].strip()
        out = run_memory_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            client.send_message(
                chat_id,
                chunk,
                reply_to_message_id=message_id if index == 0 else None,
            )
        return

    if text.startswith("/remember"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot
        raw = text[len("/remember") :].strip()
        result = run_remember_command(bot, raw)
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

        raw_args = text[len("/compact") :].strip()
        compact_result = run_compact_command(bot, raw_args)
        if compact_result == "compact unavailable":
            sessions[chat_id] = bot_factory()
            compact_result = "session compacted: session restarted."

        client.send_message(
            chat_id,
            compact_result,
            reply_to_message_id=message_id,
        )
        return

    if text.startswith("/new"):
        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot

        new_result = run_new_session_command(bot)
        if new_result == "new session unavailable":
            sessions[chat_id] = bot_factory()
            new_result = "new session started."

        client.send_message(
            chat_id,
            new_result,
            reply_to_message_id=message_id,
        )
        return

    photo_file_id = extract_largest_photo_file_id(message)
    if photo_file_id and not text.startswith("/"):
        if not text.strip():
            client.send_message(
                chat_id,
                "photo received. add a caption describing the edit, for example: "
                "'keep subject, change background to white'.",
                reply_to_message_id=message_id,
            )
            return

        bot = sessions.get(chat_id)
        if bot is None:
            bot = bot_factory()
            sessions[chat_id] = bot
        registry = getattr(bot, "tool_registry", None)
        if registry is None or not hasattr(registry, "call"):
            client.send_message(
                chat_id,
                "image edit unavailable: tool runtime is missing.",
                reply_to_message_id=message_id,
            )
            return

        try:
            file_info = client.get_file(photo_file_id)
            file_path = str(file_info.get("file_path", "")).strip()
            if not file_path:
                raise RuntimeError("telegram getFile returned empty file_path")
            image_bytes = client.download_file(file_path)
            input_image = base64.b64encode(image_bytes).decode("ascii")
        except Exception as exc:
            client.send_message(
                chat_id,
                f"image fetch error: {exc}",
                reply_to_message_id=message_id,
            )
            return

        payload_obj = {
            "mode": "image_edit",
            "prompt": text.strip(),
            "input_image": input_image,
        }
        plugin_out = str(
            registry.call("nano_banana_image", json.dumps(payload_obj, ensure_ascii=True))
        )
        if plugin_out.startswith("unknown tool:"):
            plugin_out = "image edit unavailable: install/enable src/plugins/nano_banana_image.py"

        chunks = chunk_message(plugin_out or "(empty response)")
        for index, chunk in enumerate(chunks):
            client.send_message(
                chat_id,
                chunk,
                reply_to_message_id=message_id if index == 0 else None,
            )
        send_generated_images_from_paths(
            client,
            chat_id,
            message_id,
            extract_image_saved_paths(plugin_out),
        )
        return

    bot = sessions.get(chat_id)
    if bot is None:
        bot = bot_factory()
        sessions[chat_id] = bot

    generated_image_paths: list[str] = []

    def _on_tool_result(name: str, _arguments: str, output: str) -> None:
        if name != "nano_banana_image":
            return
        generated_image_paths.extend(extract_image_saved_paths(output))

    try:
        try:
            reply = bot.ask(text, on_tool_result=_on_tool_result)
        except TypeError:
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

    send_generated_images_from_paths(
        client,
        chat_id,
        message_id,
        generated_image_paths,
    )
