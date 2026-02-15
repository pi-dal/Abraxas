import os

from dotenv import load_dotenv

DEFAULT_ENV_PATH = ".env"
DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
DEFAULT_MODEL = "glm-4.7"
DEFAULT_SKILLS_DIR = "src/skills"
DEFAULT_MEMORY_DIR = "src/memory"
DEFAULT_MEMORY_TZ = "Asia/Shanghai"
DEFAULT_MEMORY_DAILY_SYNC_TIME = "02:00"
DEFAULT_MEMORY_MICRO_SYNC_TIMES = "10:00,13:00,16:00,19:00,22:00"
DEFAULT_MEMORY_WEEKLY_COMPOUND_TIME = "22:00"
DEFAULT_MEMORY_WEEKLY_COMPOUND_WEEKDAY = 6
DEFAULT_QMD_COMMAND = "qmd"
DEFAULT_QMD_TIMEOUT_SEC = 30
DEFAULT_MEMORY_TOP_K = 6
DEFAULT_MEMORY_MAX_INJECT_CHARS = 4000
DEFAULT_NOUS_PATH = "src/core/NOUS.md"
DEFAULT_NOUS_TZ = "Asia/Shanghai"
DEFAULT_AUTO_COMPACT_MAX_TOKENS = 12000
DEFAULT_AUTO_COMPACT_KEEP_LAST_MESSAGES = 12
DEFAULT_AUTO_BRAINDUMP_ENABLED = True


def resolve_env_path(env_path: str | None = None) -> str:
    if env_path:
        return env_path
    return os.getenv("ABRAXAS_ENV_PATH", DEFAULT_ENV_PATH)


def _read_int_env(name: str, default: int, *, allow_zero: bool = False) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    if value == 0 and not allow_zero:
        return default
    return value


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def load_runtime_settings(env_path: str | None = None) -> dict[str, str | int | None]:
    load_dotenv(resolve_env_path(env_path))
    return {
        "api_key": os.getenv("API_KEY"),
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "allowed_telegram_chat_ids": os.getenv("ALLOWED_TELEGRAM_CHAT_IDS"),
        "skills_dir": os.getenv("ABRAXAS_SKILLS_DIR", DEFAULT_SKILLS_DIR),
        "memory_dir": os.getenv("ABRAXAS_MEMORY_DIR", DEFAULT_MEMORY_DIR),
        "memory_tz": os.getenv("ABRAXAS_MEMORY_TZ", DEFAULT_MEMORY_TZ),
        "memory_daily_sync_time": os.getenv(
            "ABRAXAS_MEMORY_DAILY_SYNC_TIME",
            DEFAULT_MEMORY_DAILY_SYNC_TIME,
        ),
        "memory_micro_sync_times": os.getenv(
            "ABRAXAS_MEMORY_MICRO_SYNC_TIMES",
            DEFAULT_MEMORY_MICRO_SYNC_TIMES,
        ),
        "memory_weekly_compound_time": os.getenv(
            "ABRAXAS_MEMORY_WEEKLY_COMPOUND_TIME",
            DEFAULT_MEMORY_WEEKLY_COMPOUND_TIME,
        ),
        "memory_weekly_compound_weekday": _read_int_env(
            "ABRAXAS_MEMORY_WEEKLY_COMPOUND_WEEKDAY",
            DEFAULT_MEMORY_WEEKLY_COMPOUND_WEEKDAY,
        ),
        "qmd_command": os.getenv("ABRAXAS_QMD_COMMAND", DEFAULT_QMD_COMMAND),
        "qmd_timeout_sec": _read_int_env("ABRAXAS_QMD_TIMEOUT_SEC", DEFAULT_QMD_TIMEOUT_SEC),
        "memory_top_k": _read_int_env("ABRAXAS_MEMORY_TOP_K", DEFAULT_MEMORY_TOP_K),
        "memory_max_inject_chars": _read_int_env(
            "ABRAXAS_MEMORY_MAX_INJECT_CHARS",
            DEFAULT_MEMORY_MAX_INJECT_CHARS,
        ),
        "auto_compact_max_tokens": _read_int_env(
            "ABRAXAS_AUTO_COMPACT_MAX_TOKENS",
            DEFAULT_AUTO_COMPACT_MAX_TOKENS,
            allow_zero=True,
        ),
        "auto_compact_keep_last_messages": _read_int_env(
            "ABRAXAS_AUTO_COMPACT_KEEP_LAST_MESSAGES",
            DEFAULT_AUTO_COMPACT_KEEP_LAST_MESSAGES,
        ),
        "auto_compact_instructions": os.getenv("ABRAXAS_AUTO_COMPACT_INSTRUCTIONS") or None,
        "auto_braindump_enabled": _read_bool_env(
            "ABRAXAS_AUTO_BRAINDUMP_ENABLED",
            DEFAULT_AUTO_BRAINDUMP_ENABLED,
        ),
        "nous_path": os.getenv("ABRAXAS_NOUS_PATH", DEFAULT_NOUS_PATH),
        "nous_tz": os.getenv("ABRAXAS_NOUS_TZ", DEFAULT_NOUS_TZ),
    }
