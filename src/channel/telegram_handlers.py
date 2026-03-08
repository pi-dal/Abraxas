import base64
import inspect
import itertools
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.bot import CodingBot
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
    resolve_recent_photo_paths,
    run_rci_command,
    run_remember_command,
    run_tape_command,
    run_tmux_plugin_command,
    run_yolo_command,
    run_safe_command,
    run_stop_command,
)
from core.settings import load_runtime_settings

from .telegram_client import TelegramClient, sync_telegram_commands
from .telegram_formatter import TelegramRenderedText, render_telegram_message

REMEMBER_STATE_WAIT_CONTENT = "remember_wait_content"
REMEMBER_STATE_WAIT_DESTINATION = "remember_wait_destination"
_conversation_states: dict[int, str] = {}
_conversation_data: dict[int, dict[str, str]] = {}
_telegram_draft_ids = itertools.count(1)
_latest_image_context_by_chat: dict[int, tuple[str, str]] = {}

VALID_TELEGRAM_STREAM_MODES = {"off", "partial", "block"}


class _NoopStopEvent:
    def set(self) -> None:
        return None


class _TelegramStreamSettings:
    def __init__(self, mode: str, draft_chunk_min_chars: int, draft_chunk_max_chars: int) -> None:
        self.mode = mode
        self.draft_chunk_min_chars = max(1, draft_chunk_min_chars)
        self.draft_chunk_max_chars = max(self.draft_chunk_min_chars, draft_chunk_max_chars)


class _TelegramTempSettings:
    def __init__(self, root_dir: Path, ttl_days: int) -> None:
        self.root_dir = root_dir
        self.ttl_days = ttl_days


def _resolve_telegram_stream_settings() -> _TelegramStreamSettings:
    settings = load_runtime_settings()
    raw_mode = str(settings.get("telegram_stream_mode", "partial") or "partial").strip().lower()
    mode = raw_mode if raw_mode in VALID_TELEGRAM_STREAM_MODES else "partial"
    try:
        min_chars = int(settings.get("telegram_draft_chunk_min_chars", 200) or 200)
    except Exception:
        min_chars = 200
    try:
        max_chars = int(settings.get("telegram_draft_chunk_max_chars", 800) or 800)
    except Exception:
        max_chars = 800
    return _TelegramStreamSettings(mode, min_chars, max_chars)


def _resolve_telegram_temp_settings() -> _TelegramTempSettings:
    settings = load_runtime_settings()
    root_raw = str(settings.get("telegram_temp_dir", "tmp/telegram_sessions") or "").strip()
    root_dir = Path(root_raw or "tmp/telegram_sessions").expanduser()
    try:
        ttl_days = int(settings.get("telegram_temp_ttl_days", 3) or 3)
    except Exception:
        ttl_days = 3
    if ttl_days < 0:
        ttl_days = 3
    return _TelegramTempSettings(root_dir=root_dir, ttl_days=ttl_days)


def _render_telegram_text(text: str, *, use_formatting: bool = True) -> TelegramRenderedText:
    return render_telegram_message(text, use_formatting=use_formatting)


def _visible_stream_text_length(text: str) -> int:
    content = str(text or "")
    content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", content)
    content = re.sub(r"```[\s\S]*?```", lambda match: match.group(0)[3:-3], content)
    content = re.sub(r"`([^`]+)`", r"\1", content)
    for marker in ("**", "__", "~~", "||", "*", "_"):
        content = content.replace(marker, "")
    return len(content.rstrip())


def _send_telegram_text(
    client: TelegramClient,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    reply_markup: dict[str, Any] | None = None,
    message_thread_id: int | None = None,
    use_formatting: bool = True,
) -> dict:
    rendered = _render_telegram_text(text, use_formatting=use_formatting)
    return client.send_message(
        chat_id,
        rendered.text,
        reply_to_message_id=reply_to_message_id,
        parse_mode=rendered.parse_mode,
        reply_markup=reply_markup,
        message_thread_id=message_thread_id,
    )


def _edit_telegram_text(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    use_formatting: bool = True,
) -> dict:
    rendered = _render_telegram_text(text, use_formatting=use_formatting)
    return client.edit_message_text(
        chat_id,
        message_id,
        rendered.text,
        parse_mode=rendered.parse_mode,
        reply_markup=reply_markup,
    )


class _TelegramDraftReply:
    def __init__(
        self,
        client: TelegramClient,
        chat_id: int,
        reply_to_message_id: int,
        message_thread_id: int | None = None,
        prefer_draft: bool = False,
        stream_settings: _TelegramStreamSettings | None = None,
    ) -> None:
        self.client = client
        self.chat_id = chat_id
        self.reply_to_message_id = reply_to_message_id
        self.message_thread_id = message_thread_id
        self.message_id: int | None = None
        self._last_text = ""
        self._prefer_draft = prefer_draft
        self._using_draft = False
        self._draft_id = next(_telegram_draft_ids)
        self._last_parse_mode: str | None = None
        self._last_source_text = ""
        self._stream_settings = stream_settings or _TelegramStreamSettings("partial", 200, 800)

    def _select_preview_text(self, content: str) -> str | None:
        mode = self._stream_settings.mode
        if mode == "off":
            return None
        if mode == "partial":
            return content

        previous = self._last_source_text
        current_visible_len = _visible_stream_text_length(content)
        previous_visible_len = _visible_stream_text_length(previous)
        delta = current_visible_len - previous_visible_len
        if not previous:
            if current_visible_len < self._stream_settings.draft_chunk_min_chars:
                return None
            return content
        if delta <= 0:
            return None
        if delta >= self._stream_settings.draft_chunk_max_chars:
            return content
        if delta < self._stream_settings.draft_chunk_min_chars:
            return None
        trimmed = content.rstrip()
        if trimmed.endswith((".", "!", "?", "\n", "`")):
            return content
        return None

    def update(self, text: str) -> None:
        content = str(text or "").rstrip()
        preview_source = self._select_preview_text(content)
        if not preview_source:
            return
        rendered_preview = _render_telegram_text(chunk_message(preview_source)[0])
        preview = rendered_preview.text
        if preview == self._last_text and rendered_preview.parse_mode == self._last_parse_mode:
            return
        if self._prefer_draft:
            sender = getattr(self.client, "send_message_draft", None)
            if callable(sender):
                try:
                    sender(
                        self.chat_id,
                        self._draft_id,
                        preview,
                        message_thread_id=self.message_thread_id,
                        parse_mode=rendered_preview.parse_mode,
                    )
                    self._using_draft = True
                    self._last_text = preview
                    self._last_parse_mode = rendered_preview.parse_mode
                    self._last_source_text = preview_source
                    return
                except Exception:
                    self._prefer_draft = False
        if self.message_id is None:
            result = _send_telegram_text(
                self.client,
                self.chat_id,
                preview_source,
                reply_to_message_id=self.reply_to_message_id,
                message_thread_id=self.message_thread_id,
            )
            raw_message_id = result.get("message_id") if isinstance(result, dict) else None
            if isinstance(raw_message_id, int):
                self.message_id = raw_message_id
            self._last_text = preview
            self._last_parse_mode = rendered_preview.parse_mode
            self._last_source_text = preview_source
            return
        _edit_telegram_text(
            self.client,
            self.chat_id,
            self.message_id,
            preview_source,
        )
        self._last_text = preview
        self._last_parse_mode = rendered_preview.parse_mode
        self._last_source_text = preview_source

    def finalize(self, text: str, reply_markup: dict[str, Any] | None = None) -> bool:
        content = str(text or "").strip() or "(empty response)"
        chunks = chunk_message(content)
        if self._using_draft:
            for index, chunk in enumerate(chunks):
                current_keyboard = reply_markup if index == 0 else None
                _send_telegram_text(
                    self.client,
                    self.chat_id,
                    chunk,
                    reply_to_message_id=self.reply_to_message_id if index == 0 else None,
                    reply_markup=current_keyboard,
                    message_thread_id=self.message_thread_id,
                )
            rendered_last = _render_telegram_text(chunks[-1])
            self._last_text = rendered_last.text
            self._last_parse_mode = rendered_last.parse_mode
            self._last_source_text = chunks[-1]
            return True
        if self.message_id is None:
            for index, chunk in enumerate(chunks):
                current_keyboard = reply_markup if index == 0 else None
                _send_telegram_text(
                    self.client,
                    self.chat_id,
                    chunk,
                    reply_to_message_id=self.reply_to_message_id if index == 0 else None,
                    reply_markup=current_keyboard,
                    message_thread_id=self.message_thread_id,
                )
            return False

        rendered_first = _render_telegram_text(chunks[0])
        if (
            rendered_first.text != self._last_text
            or rendered_first.parse_mode != self._last_parse_mode
            or reply_markup is not None
        ):
            _edit_telegram_text(
                self.client,
                self.chat_id,
                self.message_id,
                chunks[0],
                reply_markup=reply_markup,
            )
        for chunk in chunks[1:]:
            _send_telegram_text(
                self.client,
                self.chat_id,
                chunk,
                message_thread_id=self.message_thread_id,
            )
        rendered_last = _render_telegram_text(chunks[-1])
        self._last_text = rendered_last.text
        self._last_parse_mode = rendered_last.parse_mode
        self._last_source_text = chunks[-1]
        return True

    def clear(self) -> None:
        if self.message_id is None:
            return
        deleter = getattr(self.client, "delete_message", None)
        if callable(deleter):
            try:
                deleter(self.chat_id, self.message_id)
            except Exception:
                pass
        self.message_id = None
        self._last_text = ""


def create_chat_session_bot(bot_factory, chat_id: int) -> CodingBot:
    session_id = f"tg_{chat_id}"
    try:
        signature = inspect.signature(bot_factory)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        parameters = signature.parameters.values()
        if "session_id" in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
        ):
            return bot_factory(session_id=session_id)
        return bot_factory()

    try:
        return bot_factory(session_id=session_id)
    except TypeError:
        return bot_factory()


def get_or_create_session(
    sessions: dict[int, CodingBot],
    chat_id: int,
    bot_factory,
) -> CodingBot:
    bot = sessions.get(chat_id)
    if bot is None:
        bot = create_chat_session_bot(bot_factory, chat_id)
        sessions[chat_id] = bot
    return bot


def start_typing_feedback(
    client: TelegramClient,
    chat_id: int,
    message_thread_id: int | None = None,
):
    starter = getattr(client, "start_typing_action", None)
    if callable(starter):
        try:
            return starter(chat_id, message_thread_id=message_thread_id)
        except Exception:
            return _NoopStopEvent()
    return _NoopStopEvent()


def call_bot_ask(
    bot: CodingBot,
    text: str,
    on_tool_result: Callable[[str, str, str], None] | None = None,
    on_partial_response: Callable[[str], None] | None = None,
    user_content: Any | None = None,
) -> str:
    ask = getattr(bot, "ask")
    try:
        signature = inspect.signature(ask)
    except (TypeError, ValueError):
        signature = None

    kwargs: dict[str, Any] = {}
    if signature is not None:
        parameters = signature.parameters.values()
        accepts_var_kw = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        if accepts_var_kw or "on_tool_result" in signature.parameters:
            kwargs["on_tool_result"] = on_tool_result
        if accepts_var_kw or "on_partial_response" in signature.parameters:
            kwargs["on_partial_response"] = on_partial_response
        if accepts_var_kw or "user_content" in signature.parameters:
            kwargs["user_content"] = user_content
        return ask(text, **kwargs)

    try:
        return ask(
            text,
            on_tool_result=on_tool_result,
            on_partial_response=on_partial_response,
            user_content=user_content,
        )
    except TypeError:
        try:
            return ask(text, on_tool_result=on_tool_result, user_content=user_content)
        except TypeError:
            return ask(text)

def _has_pending_approval(bot: CodingBot | None) -> bool:
    """Check if bot has a pending tool approval awaiting user response.
    
    Args:
        bot: CodingBot instance or None
        
    Returns:
        True if there is a pending tool call awaiting approval
    """
    if bot is None:
        return False
    controller = getattr(bot, "_execution_controller", None)
    if controller is None:
        return False
    return controller.pending_tool_call is not None



def build_inline_keyboard(button_rows: list[list[tuple[str, str]]]) -> dict[str, list[list[dict[str, str]]]]:
    keyboard: list[list[dict[str, str]]] = []
    for row in button_rows:
        button_row: list[dict[str, str]] = []
        for text, callback_data in row:
            button_row.append({"text": str(text), "callback_data": str(callback_data)})
        if button_row:
            keyboard.append(button_row)
    return {"inline_keyboard": keyboard}


def _build_intercepted_keyboard(
    bot: "CodingBot",
    reply: str,
) -> "dict | None":
    """
    Build the Allow / Deny / Always Allow inline keyboard if *reply* is an
    [INTERCEPTED] message and a matching pending tool call exists on the bot.

    Layout:
        Row 1: [✅ Allow]  [❌ Deny]
        Row 2: [⚡ Always Allow (YOLO)]

    Returns the keyboard dict, or None if not applicable.
    """
    if not reply or "[INTERCEPTED]" not in reply:
        return None
    controller = getattr(bot, "_execution_controller", None)
    pending = getattr(controller, "pending_tool_call", None) if controller else None
    if pending and hasattr(pending, "id"):
        return build_inline_keyboard(
            [
                [("\u2705 Allow", f"allow_{pending.id}"), ("\u274c Deny", f"deny_{pending.id}")],
                [("\u26a1 Always Allow (YOLO)", f"always_allow_{pending.id}")],
            ]
        )
    return None


def extract_callback_payload(update: dict) -> tuple[int, int, int | None, str, str] | None:
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return None
    callback_id = str(callback_query.get("id", "")).strip()
    data = str(callback_query.get("data", "")).strip()
    message = callback_query.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    message_id = message.get("message_id")
    message_thread_id = extract_message_thread_id(message)
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return None
    if not callback_id or not data:
        return None
    return chat_id, message_id, message_thread_id, callback_id, data


def _clear_conversation(chat_id: int) -> None:
    _conversation_states.pop(chat_id, None)
    _conversation_data.pop(chat_id, None)


def _save_to_memory(content: str) -> None:
    from core.memory import memory_runtime

    timestamp = datetime.now(timezone.utc).isoformat()
    memory_runtime.append("MEMORY.md", f"\n## {timestamp}\n{content}\n")


def _save_to_braindump(content: str) -> None:
    from core.memory import memory_runtime

    timestamp = datetime.now(timezone.utc).isoformat()
    memory_runtime.append("braindump.md", f"\n### {timestamp}\n{content}\n")


def _save_to_mission_log(content: str) -> None:
    from core.memory import memory_runtime

    timestamp = datetime.now(timezone.utc).isoformat()
    memory_runtime.append("mission-log.md", f"\n- [{timestamp}] {content}\n")


def _handle_callback_query(
    chat_id: int,
    message_id: int,
    message_thread_id: int | None,
    callback_query_id: str,
    data: str,
    client: TelegramClient,
    sessions: dict[int, CodingBot],
) -> None:
    client.answer_callback_query(callback_query_id)
    
    is_always_allow = data.startswith("always_allow_")
    is_allow = is_always_allow or data.startswith("allow_")
    is_deny = data.startswith("deny_")

    if not (is_allow or is_deny):
        # Not a HITL callback — fall through to other handlers (remember:, etc.)
        pass
    else:
        bot = sessions.get(chat_id)
        if not bot:
            _edit_telegram_text(client, chat_id, message_id, "session expired.")
            return

        if is_always_allow:
            target_id = data[13:]   # len("always_allow_") == 13
        elif is_allow:
            target_id = data[6:]    # len("allow_") == 6
        else:
            target_id = data[5:]    # len("deny_") == 5

        controller = getattr(bot, "_execution_controller", None)
        pending = controller.pending_tool_call if controller else None

        if pending is None or pending.id != target_id:
            _edit_telegram_text(
                client,
                chat_id,
                message_id,
                "this approval request has expired or is invalid.",
            )
            return

        # Start typing indicator — allow/deny/always_allow executes the tool AND makes
        # an LLM call, which can take several seconds with no visual feedback otherwise.
        typing_stop = start_typing_feedback(client, chat_id, message_thread_id=message_thread_id)
        llm_reply = ""
        status_badge = ""
        try:
            if is_always_allow:
                llm_reply = bot.always_allow_pending_tool()
                status_badge = "\u26a1 Executed. YOLO mode on \u2014 all tools run without approval for this session."
            elif is_allow:
                llm_reply = bot.allow_pending_tool()
                status_badge = "\u2705 Executed."
            else:
                llm_reply = bot.deny_pending_tool()
                status_badge = "\u274c Denied."

            # Step 1: edit the original [INTERCEPTED] message to a brief closure badge,
            # removing the now-stale buttons.
            try:
                _edit_telegram_text(client, chat_id, message_id, status_badge)
            except Exception:
                _send_telegram_text(
                    client,
                    chat_id,
                    status_badge,
                    reply_to_message_id=message_id,
                    message_thread_id=message_thread_id,
                )

            # Step 2: send LLM's follow-up as a new message.
            # If the follow-up is itself a nested [INTERCEPTED] (LLM called another
            # high-risk tool after this one was approved), attach fresh buttons
            # so the user can respond \u2014 otherwise send as plain text.
            if llm_reply and llm_reply.strip():
                nested_keyboard = _build_intercepted_keyboard(bot, llm_reply)
                chunks = chunk_message(llm_reply)
                for index, chunk in enumerate(chunks):
                    current_keyboard = nested_keyboard if (nested_keyboard and index == 0) else None
                    _send_telegram_text(
                        client,
                        chat_id,
                        chunk,
                        reply_markup=current_keyboard,
                        message_thread_id=message_thread_id,
                    )

        except Exception as e:
            _edit_telegram_text(client, chat_id, message_id, f"Error while applying decision: {e}")
        finally:
            typing_stop.set()  # always stop typing, even on error
        return

    if not data.startswith("remember:"):
        return

    state = _conversation_states.get(chat_id)
    payload = _conversation_data.get(chat_id, {})
    content = str(payload.get("remember_content", "")).strip()

    if state != REMEMBER_STATE_WAIT_DESTINATION or not content:
        _edit_telegram_text(client, chat_id, message_id, "remember flow expired. run /remember again.")
        _clear_conversation(chat_id)
        return

    action = data.split(":", 1)[-1]
    try:
        if action == "memory":
            _save_to_memory(content)
            out = "saved to MEMORY.md"
        elif action == "braindump":
            _save_to_braindump(content)
            out = "saved to braindump.md"
        elif action == "mission":
            _save_to_mission_log(content)
            out = "added to mission-log.md"
        elif action == "cancel":
            out = "memory save cancelled."
        else:
            out = "unknown action."
    except Exception as exc:
        out = f"save failed: {exc}"

    _edit_telegram_text(client, chat_id, message_id, out)
    _clear_conversation(chat_id)


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
    has_photo = bool(extract_image_file_id(message))
    has_document = bool(extract_document_file_id(message))
    text = text_value.strip() if isinstance(text_value, str) else ""
    if not text and not has_photo and not has_document:
        return None
    return chat_id, message_id, text


def extract_message_thread_id(message: dict) -> int | None:
    raw = message.get("message_thread_id")
    return raw if isinstance(raw, int) else None


def extract_chat_type(message: dict) -> str:
    chat = message.get("chat")
    raw = chat.get("type") if isinstance(chat, dict) else ""
    return str(raw).strip().lower()


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


def _is_image_document(document: dict) -> bool:
    mime_type = str(document.get("mime_type", "")).strip().lower()
    if mime_type.startswith("image/"):
        return True
    file_name = str(document.get("file_name", "")).strip().lower()
    return file_name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"))


def extract_document_image_file_id(message: dict) -> str | None:
    document = message.get("document")
    if not isinstance(document, dict):
        return None
    if not _is_image_document(document):
        return None
    file_id = str(document.get("file_id", "")).strip()
    return file_id or None


def extract_document_file_id(message: dict) -> str | None:
    document = message.get("document")
    if not isinstance(document, dict):
        return None
    file_id = str(document.get("file_id", "")).strip()
    return file_id or None


def extract_image_file_id(message: dict) -> str | None:
    return extract_largest_photo_file_id(message) or extract_document_image_file_id(message)


def extract_image_source(message: dict) -> tuple[str, dict] | None:
    file_id = extract_image_file_id(message)
    if file_id:
        return file_id, message
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        reply_file_id = extract_image_file_id(reply)
        if reply_file_id:
            return reply_file_id, reply
    return None


def _sanitize_attachment_name(value: str) -> str:
    name = Path(str(value or "").strip()).name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe or "attachment"


def _extract_document_extension(document: dict, file_path: str) -> str:
    file_name = str(document.get("file_name", "")).strip()
    file_name_suffix = Path(file_name).suffix.strip().lower()
    if file_name_suffix:
        return file_name_suffix
    path_suffix = Path(str(file_path or "").strip()).suffix.strip().lower()
    if path_suffix:
        return path_suffix
    mime_type = str(document.get("mime_type", "")).strip().lower()
    if mime_type == "application/pdf":
        return ".pdf"
    return ""


def _cleanup_expired_telegram_temp_sessions(settings: _TelegramTempSettings) -> None:
    if settings.ttl_days <= 0:
        return
    base_dir = settings.root_dir
    if not base_dir.exists() or not base_dir.is_dir():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - settings.ttl_days * 24 * 60 * 60
    for item in base_dir.iterdir():
        if not item.is_dir() or not item.name.startswith("tg_"):
            continue
        try:
            mtime = item.stat().st_mtime
        except Exception:
            continue
        if mtime >= cutoff:
            continue
        shutil.rmtree(item, ignore_errors=True)


def _store_document_attachment(
    client: TelegramClient,
    chat_id: int,
    document: dict,
    *,
    temp_settings: _TelegramTempSettings,
) -> tuple[Path, str, str]:
    file_id = str(document.get("file_id", "")).strip()
    if not file_id:
        raise RuntimeError("telegram document has empty file_id")
    file_info = client.get_file(file_id)
    file_path = str(file_info.get("file_path", "")).strip()
    if not file_path:
        raise RuntimeError("telegram getFile returned empty file_path")

    content = client.download_file(file_path)
    original_name = str(document.get("file_name", "")).strip() or Path(file_path).name or file_id
    safe_name = _sanitize_attachment_name(original_name)
    ext = _extract_document_extension(document, file_path)
    if ext and Path(safe_name).suffix.lower() != ext:
        safe_name = f"{safe_name}{ext}"

    session_dir = temp_settings.root_dir / f"tg_{chat_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = session_dir / f"{timestamp}_{safe_name}"
    suffix = 1
    while destination.exists():
        destination = session_dir / f"{timestamp}_{suffix}_{safe_name}"
        suffix += 1
    destination.write_bytes(content)
    try:
        os.utime(session_dir, None)
        if temp_settings.root_dir.exists():
            os.utime(temp_settings.root_dir, None)
    except Exception:
        pass

    mime_type = str(document.get("mime_type", "")).strip().lower() or "application/octet-stream"
    return destination.resolve(), original_name, mime_type


def _guess_mime_type_from_path(file_path: str) -> str:
    lowered = str(file_path or "").strip().lower()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith(".webp"):
        return "image/webp"
    if lowered.endswith(".gif"):
        return "image/gif"
    if lowered.endswith(".heic"):
        return "image/heic"
    return "image/jpeg"


def _image_extension_from_mime_type(mime_type: str) -> str:
    lowered = str(mime_type or "").strip().lower()
    if lowered == "image/png":
        return ".png"
    if lowered == "image/webp":
        return ".webp"
    if lowered == "image/gif":
        return ".gif"
    if lowered == "image/heic":
        return ".heic"
    return ".jpg"


def _store_photo_attachment_from_bytes(
    *,
    chat_id: int,
    content: bytes,
    mime_type: str,
    temp_settings: _TelegramTempSettings,
) -> Path:
    session_dir = temp_settings.root_dir / f"tg_{chat_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ext = _image_extension_from_mime_type(mime_type)
    destination = session_dir / f"{timestamp}_telegram_photo{ext}"
    suffix = 1
    while destination.exists():
        destination = session_dir / f"{timestamp}_{suffix}_telegram_photo{ext}"
        suffix += 1
    destination.write_bytes(content)
    try:
        os.utime(session_dir, None)
        if temp_settings.root_dir.exists():
            os.utime(temp_settings.root_dir, None)
    except Exception:
        pass
    return destination.resolve()


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


def parse_image_tool_payload(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        obj = json.loads(str(text))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def extract_image_paths_and_urls(text: str) -> tuple[list[str], list[str]]:
    payload = parse_image_tool_payload(text)
    paths: list[str] = []
    urls: list[str] = []

    if isinstance(payload, dict):
        raw_images = payload.get("images")
        if isinstance(raw_images, list):
            for item in raw_images:
                if not isinstance(item, dict):
                    continue
                local_path = str(item.get("local_path", "")).strip()
                if local_path:
                    paths.append(local_path)
                public_url = str(item.get("public_url", "")).strip()
                if public_url:
                    urls.append(public_url)

        raw_results = payload.get("results")
        if isinstance(raw_results, list):
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                images = result.get("images")
                if not isinstance(images, list):
                    continue
                for item in images:
                    if not isinstance(item, dict):
                        continue
                    local_path = str(item.get("local_path", "")).strip()
                    if local_path:
                        paths.append(local_path)
                    public_url = str(item.get("public_url", "")).strip()
                    if public_url:
                        urls.append(public_url)

    if not paths:
        paths.extend(extract_image_saved_paths(text))

    dedup_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        dedup_paths.append(path)

    dedup_urls: list[str] = []
    seen_urls: set[str] = set()
    for url in urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        dedup_urls.append(url)

    return dedup_paths, dedup_urls


def extract_document_paths_and_urls(text: str) -> tuple[list[str], list[str]]:
    payload = parse_image_tool_payload(text)
    paths: list[str] = []
    urls: list[str] = []
    if isinstance(payload, dict):
        raw_files = payload.get("files")
        if isinstance(raw_files, list):
            for item in raw_files:
                if not isinstance(item, dict):
                    continue
                local_path = str(item.get("local_path", "")).strip()
                if local_path:
                    paths.append(local_path)
                public_url = str(item.get("public_url", "")).strip()
                if public_url:
                    urls.append(public_url)
    dedup_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        dedup_paths.append(path)
    dedup_urls: list[str] = []
    seen_urls: set[str] = set()
    for url in urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        dedup_urls.append(url)
    return dedup_paths, dedup_urls


def format_image_tool_summary(text: str) -> str:
    payload = parse_image_tool_payload(text)
    if not isinstance(payload, dict):
        return str(text)
    if not bool(payload.get("ok", False)):
        error = str(payload.get("error", "")).strip() or "image generation failed."
        return error
    paths, _ = extract_image_paths_and_urls(text)
    mode = str(payload.get("mode", "image")).strip()
    return f"{mode} done: {len(paths)} image(s) generated."


def send_generated_documents_from_paths(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    document_paths: list[str],
    document_urls: list[str] | None = None,
    message_thread_id: int | None = None,
) -> None:
    seen: set[str] = set()
    for document_path in document_paths:
        if document_path in seen:
            continue
        seen.add(document_path)
        resolved = Path(document_path).expanduser()
        if not resolved.exists() or not resolved.is_file():
            _send_telegram_text(
                client,
                chat_id,
                f"document send skipped: file not found: {resolved}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
            continue
        try:
            client.send_document(
                chat_id,
                str(resolved),
                caption=f"generated file\nsource: {resolved}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
        except Exception as exc:
            _send_telegram_text(
                client,
                chat_id,
                f"document send error: {exc}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )

    for url in document_urls or []:
        if url in seen:
            continue
        seen.add(url)
        try:
            client.send_document(
                chat_id,
                url,
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
        except Exception as exc:
            _send_telegram_text(
                client,
                chat_id,
                f"document send error: {exc}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )


def send_generated_images_from_paths(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    image_paths: list[str],
    image_urls: list[str] | None = None,
    include_local_addresses: bool = False,
    message_thread_id: int | None = None,
) -> None:
    sent_paths: list[str] = []
    sent_urls: list[str] = []
    seen: set[str] = set()
    for image_path in image_paths:
        if image_path in seen:
            continue
        seen.add(image_path)
        resolved = Path(image_path).expanduser()
        if not resolved.exists() or not resolved.is_file():
            _send_telegram_text(
                client,
                chat_id,
                f"image send skipped: file not found: {resolved}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
            continue
        try:
            client.send_photo(
                chat_id,
                str(resolved),
                caption=f"generated image\nsource: {resolved}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
            sent_paths.append(str(resolved))
        except Exception as exc:
            _send_telegram_text(
                client,
                chat_id,
                f"image send error: {exc}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )

    address_lines: list[str] = []
    if include_local_addresses and sent_paths:
        address_lines.append("image addresses:")
        for path in sent_paths:
            address_lines.append(f"- {path}")
    for url in image_urls or []:
        if url in seen:
            continue
        seen.add(url)
        try:
            client.send_photo(
                chat_id,
                url,
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
            sent_urls.append(url)
        except Exception as exc:
            _send_telegram_text(
                client,
                chat_id,
                f"image send error: {exc}",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
    for url in (image_urls or []):
        if url in sent_urls:
            continue
        if not address_lines:
            address_lines.append("image addresses:")
        address_lines.append(f"- {url}")
    if address_lines:
        _send_telegram_text(
            client,
            chat_id,
            "\n".join(address_lines),
            reply_to_message_id=message_id,
            message_thread_id=message_thread_id,
        )


def process_update(
    update: dict,
    sessions: dict[int, CodingBot],
    client: TelegramClient,
    bot_factory,
    allowed_chat_ids: set[int] | None,
) -> None:
    callback_payload = extract_callback_payload(update)
    if callback_payload is not None:
        chat_id, message_id, message_thread_id, callback_query_id, data = callback_payload
        if allowed_chat_ids is not None and chat_id not in allowed_chat_ids:
            return
        _handle_callback_query(
            chat_id,
            message_id,
            message_thread_id,
            callback_query_id,
            data,
            client,
            sessions,
        )
        return

    message = update.get("message")
    if not isinstance(message, dict):
        return
    payload = extract_message_payload(update)
    if payload is None:
        return
    chat_id, message_id, text = payload
    message_thread_id = extract_message_thread_id(message)
    chat_type = extract_chat_type(message)
    prefer_draft = chat_type == "private"
    if allowed_chat_ids is not None and chat_id not in allowed_chat_ids:
        return
    temp_settings = _resolve_telegram_temp_settings()
    _cleanup_expired_telegram_temp_sessions(temp_settings)

    def _send(
        text_value: str,
        *,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict:
        if parse_mode is not None:
            return client.send_message(
                chat_id,
                text_value,
                reply_to_message_id=reply_to_message_id,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                message_thread_id=message_thread_id,
            )
        return _send_telegram_text(
            client,
            chat_id,
            text_value,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
            message_thread_id=message_thread_id,
        )

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
        _send(build_help_text(), reply_to_message_id=message_id)
        return

    # /allow and /deny are button-only in Telegram — text commands are not wired here.
    # Guard against manual typing to avoid the message falling through to the LLM.
    if text in {"/allow", "/deny"}:
        bot = sessions.get(chat_id)
        controller = getattr(bot, "_execution_controller", None) if bot else None
        has_pending = controller is not None and controller.pending_tool_call is not None
        if has_pending:
            _send_telegram_text(
                client,
                chat_id,
                "Use the ✅ Allow / ❌ Deny buttons on the intercepted message above.",
                reply_to_message_id=message_id,
                message_thread_id=message_thread_id,
            )
        else:
            _send("No pending tool call to approve or deny.", reply_to_message_id=message_id)
        return

    # ── HITL execution mode commands ────────────────────────────────────────
    if text in {"/yolo"}:
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        out = run_yolo_command(bot)
        _send(out, reply_to_message_id=message_id)
        return

    if text in {"/safe"}:
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        out = run_safe_command(bot)
        _send(out, reply_to_message_id=message_id)
        return

    if text in {"/stop"}:
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        out = run_stop_command(bot)
        _send(out, reply_to_message_id=message_id)
        return

    if text.startswith("/handoff"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/handoff"):].strip()
        out = run_handoff_command(bot, raw)
        _send(out, reply_to_message_id=message_id)
        return

    if text.startswith("/checkpoint"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/checkpoint"):].strip()
        out = run_checkpoint_command(bot, raw)
        _send(out, reply_to_message_id=message_id)
        return

    if text.startswith("/rci"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/rci"):].strip()
        out = run_rci_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
        return

    if text.startswith("/tape"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/tape"):].strip()
        out = run_tape_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
        return

    if text.startswith("/commands"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        out = build_commands_text(bot=bot)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
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
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
        return

    if text.startswith("/sync_commands"):
        ok = sync_telegram_commands(client)
        sync_text = (
            "command menu synced with Telegram."
            if ok
            else "command menu sync failed. check token and bot permissions."
        )
        _send(sync_text, reply_to_message_id=message_id)
        return

    if text.startswith("/tmux"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/tmux") :].strip()
        out = run_tmux_plugin_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
        return

    if text.startswith("/memory"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/memory") :].strip()
        out = run_memory_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
        return

    if text.startswith("/photos"):
        raw = text[len("/photos") :].strip()
        paths, error = resolve_recent_photo_paths(raw)
        if error:
            _send(error, reply_to_message_id=message_id)
            return
        send_generated_images_from_paths(
            client,
            chat_id,
            message_id,
            paths,
            include_local_addresses=True,
            message_thread_id=message_thread_id,
        )
        return

    if text.startswith("/compact"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/compact") :].strip()
        out = run_compact_command(bot, raw)
        chunks = chunk_message(out)
        for index, chunk in enumerate(chunks):
            _send(chunk, reply_to_message_id=message_id if index == 0 else None)
        return

    if text.startswith("/remember"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)
        raw = text[len("/remember") :].strip()
        if not raw:
            _conversation_states[chat_id] = REMEMBER_STATE_WAIT_CONTENT
            _conversation_data[chat_id] = {}
            _send("what should i remember? reply with the memory content.", reply_to_message_id=message_id)
            return
        result = run_remember_command(bot, raw)
        _send(result, reply_to_message_id=message_id)
        return

    state = _conversation_states.get(chat_id)
    if state == REMEMBER_STATE_WAIT_CONTENT and not text.startswith("/"):
        note = text.strip()
        if not note:
            _send("empty memory ignored. send text or /remember to restart.", reply_to_message_id=message_id)
            return
        _conversation_data[chat_id] = {"remember_content": note}
        _conversation_states[chat_id] = REMEMBER_STATE_WAIT_DESTINATION
        keyboard = build_inline_keyboard(
            [
                [("Save MEMORY.md", "remember:memory")],
                [("Save braindump.md", "remember:braindump")],
                [("Add mission-log.md", "remember:mission")],
                [("Cancel", "remember:cancel")],
            ]
        )
        _send("choose where to save:", reply_to_message_id=message_id, reply_markup=keyboard)
        return

    if text.startswith("/new"):
        bot = get_or_create_session(sessions, chat_id, bot_factory)

        new_result = run_new_session_command(bot)
        if new_result == "new session unavailable":
            if _has_pending_approval(bot):
                new_result = "new session unavailable: cannot restart session with pending tool approval. use /stop to deny the pending tool first."
            else:
                sessions[chat_id] = create_chat_session_bot(bot_factory, chat_id)
                new_result = "new session started."

        _latest_image_context_by_chat.pop(chat_id, None)
        _send(new_result, reply_to_message_id=message_id)
        return

    if text.startswith("/"):
        _send("unknown command. use /help.", reply_to_message_id=message_id)
        return

    bot = get_or_create_session(sessions, chat_id, bot_factory)
    stream_settings = _resolve_telegram_stream_settings()
    user_content: Any | None = None
    user_text = text

    document = message.get("document")
    document_file_id = extract_document_file_id(message)
    photo_source = extract_image_source(message)
    photo_file_id = photo_source[0] if photo_source is not None else None
    photo_source_message = photo_source[1] if photo_source is not None else message
    if photo_file_id and not text.startswith("/"):
        try:
            file_info = client.get_file(photo_file_id)
            file_path = str(file_info.get("file_path", "")).strip()
            if not file_path:
                raise RuntimeError("telegram getFile returned empty file_path")
            image_bytes = client.download_file(file_path)
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            mime_type = _guess_mime_type_from_path(file_path)
            source_document = photo_source_message.get("document")
            if isinstance(source_document, dict):
                document_mime_type = str(source_document.get("mime_type", "")).strip().lower()
                if document_mime_type.startswith("image/"):
                    mime_type = document_mime_type
            local_image_path = _store_photo_attachment_from_bytes(
                chat_id=chat_id,
                content=image_bytes,
                mime_type=mime_type,
                temp_settings=temp_settings,
            )
        except Exception as exc:
            _send(f"image fetch error: {exc}", reply_to_message_id=message_id)
            return

        prompt_text = text.strip()
        if not prompt_text:
            _latest_image_context_by_chat.pop(chat_id, None)
            _send(
                "photo received. send a caption or follow-up text to describe the edit you want.",
                reply_to_message_id=message_id,
            )
            return

        registry = getattr(bot, "tool_registry", None)
        if registry is not None and hasattr(registry, "call"):
            payload = {
                "mode": "image_edit",
                "prompt": prompt_text,
                "input_image": str(local_image_path),
            }
            output = str(registry.call("nano_banana_image", json.dumps(payload, ensure_ascii=False)))
            paths, urls = extract_image_paths_and_urls(output)
            send_generated_images_from_paths(
                client,
                chat_id,
                message_id,
                paths,
                urls,
                include_local_addresses=True,
                message_thread_id=message_thread_id,
            )
            return

        prompt_text = text.strip() or "Please analyze this image first."
        user_text = prompt_text
        user_content = [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
        ]
        _latest_image_context_by_chat[chat_id] = (mime_type, image_b64)
    elif document_file_id and isinstance(document, dict) and not text.startswith("/"):
        try:
            local_path, original_name, mime_type = _store_document_attachment(
                client,
                chat_id,
                document,
                temp_settings=temp_settings,
            )
        except Exception as exc:
            _send(f"file fetch error: {exc}", reply_to_message_id=message_id)
            return
        prompt_text = text.strip() or "Please analyze this file first."
        user_text = (
            f"{prompt_text}\n\n"
            f"[Telegram attachment]\n"
            f"- original_name: {original_name}\n"
            f"- mime_type: {mime_type}\n"
            f"- local_path: {local_path}\n"
            "Use available tools to open and process this local file path."
        )
    elif not text.startswith("/"):
        prompt_text = text.strip() or "Please analyze this image first."
        latest_image = _latest_image_context_by_chat.pop(chat_id, None)
        if latest_image is not None:
            mime_type, image_b64 = latest_image
            user_text = prompt_text
            user_content = [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
            ]

    generated_image_paths: list[str] = []
    generated_image_urls: list[str] = []
    generated_document_paths: list[str] = []
    generated_document_urls: list[str] = []
    draft_reply = _TelegramDraftReply(
        client,
        chat_id,
        message_id,
        message_thread_id=message_thread_id,
        prefer_draft=prefer_draft,
        stream_settings=stream_settings,
    )

    def _on_tool_result(name: str, _arguments: str, output: str) -> None:
        if name in {"nano_banana_image", "send_telegram_photo"}:
            paths, urls = extract_image_paths_and_urls(output)
            generated_image_paths.extend(paths)
            generated_image_urls.extend(urls)
            return
        if name == "send_telegram_file":
            paths, urls = extract_document_paths_and_urls(output)
            generated_document_paths.extend(paths)
            generated_document_urls.extend(urls)
            return

    typing_stop = start_typing_feedback(client, chat_id, message_thread_id=message_thread_id)
    try:
        reply = call_bot_ask(
            bot,
            user_text,
            on_tool_result=_on_tool_result,
            on_partial_response=draft_reply.update,
            user_content=user_content,
        )
    except Exception as exc:
        reply = f"bot error: {exc}"
    finally:
        typing_stop.set()

    if (
        not generated_image_paths
        and not generated_image_urls
        and not generated_document_paths
        and not generated_document_urls
    ):
        # Build Allow/Deny keyboard if this reply is an interception prompt.
        keyboard = _build_intercepted_keyboard(bot, reply)
        draft_reply.finalize(reply or "(empty response)", reply_markup=keyboard)
    else:
        draft_reply.finalize(reply or "(empty response)")

    send_generated_images_from_paths(
        client,
        chat_id,
        message_id,
        generated_image_paths,
        generated_image_urls,
        include_local_addresses=True,
        message_thread_id=message_thread_id,
    )
    send_generated_documents_from_paths(
        client,
        chat_id,
        message_id,
        generated_document_paths,
        generated_document_urls,
        message_thread_id=message_thread_id,
    )
