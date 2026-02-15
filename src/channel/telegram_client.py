import json
import urllib.error
import urllib.request
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
