import json
import os

from core.tools import ToolPlugin


def _env_path() -> str:
    return os.getenv("ABRAXAS_ENV_PATH", ".env")


def _read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _write_env(path: str, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    content = "\n".join(lines)
    if content:
        content += "\n"
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


def _normalize_chat_ids(raw: str) -> list[str]:
    items = [item.strip() for item in raw.split(",")]
    items = [item for item in items if item]
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique


def _show(values: dict[str, str]) -> str:
    payload = {
        "telegram_bot_token": values.get("TELEGRAM_BOT_TOKEN"),
        "allowed_telegram_chat_ids": values.get("ALLOWED_TELEGRAM_CHAT_IDS"),
    }
    return json.dumps(payload, ensure_ascii=True)


def _handle(payload: dict) -> str:
    action = str(payload.get("action", "show")).strip()
    path = _env_path()
    values = _read_env(path)

    if action == "show":
        return _show(values)

    if action == "set_token":
        token = str(payload.get("token", "")).strip()
        if not token:
            return "telegram_config error: token is required"
        values["TELEGRAM_BOT_TOKEN"] = token
        _write_env(path, values)
        return "telegram_config updated: TELEGRAM_BOT_TOKEN"

    if action == "set_allowed_chat_ids":
        chat_ids = str(payload.get("chat_ids", "")).strip()
        normalized = _normalize_chat_ids(chat_ids)
        if normalized:
            values["ALLOWED_TELEGRAM_CHAT_IDS"] = ",".join(normalized)
        else:
            values.pop("ALLOWED_TELEGRAM_CHAT_IDS", None)
        _write_env(path, values)
        return "telegram_config updated: ALLOWED_TELEGRAM_CHAT_IDS"

    if action == "add_allowed_chat_id":
        chat_id = str(payload.get("chat_id", "")).strip()
        if not chat_id:
            return "telegram_config error: chat_id is required"
        current = _normalize_chat_ids(values.get("ALLOWED_TELEGRAM_CHAT_IDS", ""))
        if chat_id not in current:
            current.append(chat_id)
        values["ALLOWED_TELEGRAM_CHAT_IDS"] = ",".join(current)
        _write_env(path, values)
        return "telegram_config updated: ALLOWED_TELEGRAM_CHAT_IDS"

    if action == "remove_allowed_chat_id":
        chat_id = str(payload.get("chat_id", "")).strip()
        if not chat_id:
            return "telegram_config error: chat_id is required"
        current = _normalize_chat_ids(values.get("ALLOWED_TELEGRAM_CHAT_IDS", ""))
        current = [item for item in current if item != chat_id]
        if current:
            values["ALLOWED_TELEGRAM_CHAT_IDS"] = ",".join(current)
        else:
            values.pop("ALLOWED_TELEGRAM_CHAT_IDS", None)
        _write_env(path, values)
        return "telegram_config updated: ALLOWED_TELEGRAM_CHAT_IDS"

    if action == "clear_allowed_chat_ids":
        values.pop("ALLOWED_TELEGRAM_CHAT_IDS", None)
        _write_env(path, values)
        return "telegram_config updated: ALLOWED_TELEGRAM_CHAT_IDS"

    return f"telegram_config error: unknown action: {action}"


def register(registry) -> None:
    registry.register(
        ToolPlugin(
            name="telegram_config",
            description="Read or update Telegram settings in .env.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "show",
                            "set_token",
                            "set_allowed_chat_ids",
                            "add_allowed_chat_id",
                            "remove_allowed_chat_id",
                            "clear_allowed_chat_ids",
                        ],
                    },
                    "token": {"type": "string"},
                    "chat_ids": {"type": "string"},
                    "chat_id": {"type": "string"},
                },
                "required": ["action"],
            },
            handler=_handle,
        )
    )

