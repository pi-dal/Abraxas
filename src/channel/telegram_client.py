import json
import mimetypes
import threading
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from core.commands import DEFAULT_TELEGRAM_COMMANDS

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, token: str, request_timeout: int = 35, max_typing_workers: int = 5):
        if not token:
            raise ValueError("telegram token is required")
        self.base_url = f"{TELEGRAM_API_BASE}/bot{token}"
        self.request_timeout = request_timeout
        
        # Global shared thread pool for typing actions (limits concurrency and resource usage)
        self._typing_executor = ThreadPoolExecutor(
            max_workers=max_typing_workers,
            thread_name_prefix="telegram_typing"
        )
        self._typing_lock = threading.Lock()
        self._active_typing_tasks: dict[int, threading.Event] = {}

    def __del__(self):
        """Cleanup: shutdown thread pool on client destruction."""
        try:
            self._typing_executor.shutdown(wait=False)
        except Exception:
            pass  # Silently fail during cleanup

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

    def _post_multipart(
        self,
        method: str,
        fields: dict[str, str],
        file_field: str,
        file_path: str,
    ) -> Any:
        boundary = f"----AbraxasBoundary{uuid.uuid4().hex}"
        body = bytearray()

        def _append_text(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
            )
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        for key, value in fields.items():
            _append_text(key, value)

        path_obj = Path(file_path)
        filename = path_obj.name
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_bytes = path_obj.read_bytes()

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram request failed: {exc}") from exc
        data = json.loads(raw)
        if not data.get("ok"):
            raise RuntimeError(f"telegram api error: {data.get('description', 'unknown')}")
        return data.get("result", {})

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._post("getUpdates", payload)
        if isinstance(result, list):
            return result
        return []

    def get_file(self, file_id: str) -> dict:
        payload = {"file_id": str(file_id)}
        result = self._post("getFile", payload)
        return result if isinstance(result, dict) else {}

    def download_file(self, file_path: str) -> bytes:
        path_text = str(file_path).strip()
        if not path_text:
            raise RuntimeError("telegram file path is empty")
        token = self.base_url.rsplit("/bot", 1)[-1]
        url = f"{TELEGRAM_API_BASE}/file/bot{token}/{path_text}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram file download failed: {exc}") from exc

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        message_thread_id: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        result = self._post("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    def send_message_draft(
        self,
        chat_id: int,
        draft_id: int,
        text: str,
        message_thread_id: int | None = None,
        parse_mode: str | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": int(draft_id),
            "text": str(text),
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = self._post("sendMessageDraft", payload)
        return bool(result)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._post("editMessageText", payload)
        return result if isinstance(result, dict) else {}

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        result = self._post("deleteMessage", payload)
        return bool(result)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        payload: dict[str, Any] = {"callback_query_id": str(callback_query_id)}
        if text:
            payload["text"] = text
        if show_alert is not None:
            payload["show_alert"] = bool(show_alert)
        result = self._post("answerCallbackQuery", payload)
        return bool(result)

    def send_chat_action(
        self,
        chat_id: int,
        action: str = "typing",
        message_thread_id: int | None = None,
    ) -> None:
        """Send a chat action (e.g., 'typing', 'upload_photo') to show user activity.

        Args:
            chat_id: Target chat ID
            action: Action type (typing, upload_photo, record_video, etc.)

        Telegram API documentation:
        - Actions: typing, upload_photo, record_video, upload_video, record_audio, upload_audio,
                   upload_document, find_location, record_video_note, upload_video_note
        - The action lasts for ~5 seconds, so needs to be refreshed periodically
        """
        payload = {
            "chat_id": chat_id,
            "action": action
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        try:
            self._post("sendChatAction", payload)
        except Exception:
            pass  # Silently fail - chat action is not critical for functionality

    def start_typing_action(
        self,
        chat_id: int,
        message_thread_id: int | None = None,
    ) -> threading.Event:
        """Start background typing action in shared thread pool.
        
        Args:
            chat_id: Target chat ID
        
        Returns:
            stop_event: Threading event to signal the typing loop to stop
        
        Example:
            >>> stop_event = client.start_typing_action(chat_id)
            >>> try:
            >>>     reply = bot.ask(text)
            >>> finally:
            >>>     stop_event.set()
        """
        stop_event = threading.Event()
        
        def _typing_loop():
            """Background task: send typing action every 8 seconds."""
            while not stop_event.is_set():
                try:
                    self.send_chat_action(chat_id, "typing", message_thread_id=message_thread_id)
                except Exception:
                    pass  # Silently fail, keep loop alive
                # Wait 8 seconds or until stopped
                stop_event.wait(8.0)
        
        # Submit to shared thread pool (limits concurrency)
        self._typing_executor.submit(_typing_loop)
        
        # Track active task per chat_id (prevents duplicate typing loops)
        with self._typing_lock:
            # Cancel previous task for same chat_id if exists
            if chat_id in self._active_typing_tasks:
                self._active_typing_tasks[chat_id].set()
            self._active_typing_tasks[chat_id] = stop_event
        
        return stop_event

    def stop_typing_action(self, chat_id: int) -> None:
        """Stop typing action for a specific chat_id.
        
        Args:
            chat_id: Target chat ID
        """
        with self._typing_lock:
            if chat_id in self._active_typing_tasks:
                self._active_typing_tasks[chat_id].set()
                del self._active_typing_tasks[chat_id]

    def send_photo(
        self,
        chat_id: int,
        photo: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> dict:
        photo_ref = str(photo).strip()
        if not photo_ref:
            raise ValueError("photo is required")

        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        if reply_to_message_id is not None:
            fields["reply_to_message_id"] = str(reply_to_message_id)
        if message_thread_id is not None:
            fields["message_thread_id"] = str(message_thread_id)

        if photo_ref.startswith(("http://", "https://")):
            payload: dict[str, str | int] = {"chat_id": chat_id, "photo": photo_ref}
            if caption:
                payload["caption"] = caption
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            result = self._post("sendPhoto", payload)
            return result if isinstance(result, dict) else {}

        path = Path(photo_ref)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"photo path not found: {photo_ref}")

        result = self._post_multipart("sendPhoto", fields, "photo", str(path))
        return result if isinstance(result, dict) else {}

    def send_document(
        self,
        chat_id: int,
        document: str,
        caption: str | None = None,
        filename: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> dict:
        """Send a document/file to Telegram chat.
        
        Args:
            chat_id: Target chat ID
            document: Local file path, HTTP(S) URL, or file_id string
            caption: Optional caption text
            filename: Optional filename (for URL/file_id)
            reply_to_message_id: Optional message ID to reply to
        
        Returns:
            API response dict
        """
        doc_ref = str(document).strip()
        if not doc_ref:
            raise ValueError("document is required")

        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        if reply_to_message_id is not None:
            fields["reply_to_message_id"] = str(reply_to_message_id)
        if message_thread_id is not None:
            fields["message_thread_id"] = str(message_thread_id)

        # URL or file_id
        if doc_ref.startswith(("http://", "https://")):
            payload: dict[str, str | int] = {"chat_id": chat_id, "document": doc_ref}
            if caption:
                payload["caption"] = caption
            if filename:
                payload["filename"] = filename
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            result = self._post("sendDocument", payload)
            return result if isinstance(result, dict) else {}

        # Local file path
        path = Path(doc_ref)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"document path not found: {doc_ref}")

        result = self._post_multipart("sendDocument", fields, "document", str(path))
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
