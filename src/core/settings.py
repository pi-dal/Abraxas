import os

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
DEFAULT_MODEL = "glm-4.5"


def load_settings(env_path: str = ".env") -> dict[str, str | None]:
    load_dotenv(env_path)
    api_key = os.getenv("API_KEY")
    return {"api_key": api_key, "base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL}


def load_telegram_settings(env_path: str = ".env") -> dict[str, str | None]:
    load_dotenv(env_path)
    return {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "allowed_telegram_chat_ids": os.getenv("ALLOWED_TELEGRAM_CHAT_IDS"),
    }

