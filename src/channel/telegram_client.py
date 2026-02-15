import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from core.commands import DEFAULT_TELEGRAM_COMMANDS

TELEGRAM_API_BASE = "https://api.telegram.org"


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

    def send_photo(
        self,
        chat_id: int,
        photo: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        photo_ref = str(photo).strip()
        if not photo_ref:
            raise ValueError("photo is required")

        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        if reply_to_message_id is not None:
            fields["reply_to_message_id"] = str(reply_to_message_id)

        if photo_ref.startswith(("http://", "https://")):
            payload: dict[str, str | int] = {"chat_id": chat_id, "photo": photo_ref}
            if caption:
                payload["caption"] = caption
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            result = self._post("sendPhoto", payload)
            return result if isinstance(result, dict) else {}

        path = Path(photo_ref)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"photo path not found: {photo_ref}")

        result = self._post_multipart("sendPhoto", fields, "photo", str(path))
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
