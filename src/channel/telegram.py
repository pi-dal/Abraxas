import json
import time
import urllib.error
import urllib.request

from core.bot import CodingBot
from core.registry import ReloadableToolRegistry, create_reloadable_tool_registry
from core.settings import load_settings, load_telegram_settings

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, token: str, request_timeout: int = 35):
        if not token:
            raise ValueError("telegram token is required")
        self.base_url = f"{TELEGRAM_API_BASE}/bot{token}"
        self.request_timeout = request_timeout

    def _post(self, method: str, payload: dict) -> dict | list:
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

    if text in {"/start", "/help"}:
        client.send_message(
            chat_id,
            "Abraxas is online. Send your task directly and I will respond here.",
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


def run_telegram_bot(
    token: str,
    allowed_chat_ids: set[int] | None = None,
    poll_timeout: int = 25,
    idle_sleep: float = 0.2,
    bot_factory=CodingBot,
    tool_registry: ReloadableToolRegistry | None = None,
) -> None:
    client = TelegramClient(token)
    sessions: dict[int, CodingBot] = {}
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
