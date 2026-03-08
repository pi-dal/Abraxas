import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency path
    def load_dotenv(path: str | None = None) -> bool:  # type: ignore[no-redef]
        if not path or not os.path.exists(path):
            return False
        loaded = False
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                if not key:
                    continue
                os.environ.setdefault(key, value)
                loaded = True
        return loaded

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
DEFAULT_CHECKPOINT_TOKEN_THRESHOLD = 12000
DEFAULT_CHECKPOINT_RECENT_ENTRIES = 24
DEFAULT_CONTEXT_RECENT_ENTRIES = 96
DEFAULT_AUTO_BRAINDUMP_ENABLED = True
DEFAULT_TELEGRAM_STREAM_MODE = "partial"
DEFAULT_TELEGRAM_DRAFT_CHUNK_MIN_CHARS = 200
DEFAULT_TELEGRAM_DRAFT_CHUNK_MAX_CHARS = 800
DEFAULT_TELEGRAM_TEMP_DIR = "tmp/telegram_sessions"
DEFAULT_TELEGRAM_TEMP_TTL_DAYS = 3


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
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or DEFAULT_BASE_URL
    model = os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL
    return {
        "api_key": os.getenv("API_KEY"),
        "base_url": base_url,
        "model": model,
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
        "checkpoint_token_threshold": _read_int_env(
            "ABRAXAS_CHECKPOINT_TOKEN_THRESHOLD",
            DEFAULT_CHECKPOINT_TOKEN_THRESHOLD,
            allow_zero=True,
        ),
        "checkpoint_recent_entries": _read_int_env(
            "ABRAXAS_CHECKPOINT_RECENT_ENTRIES",
            DEFAULT_CHECKPOINT_RECENT_ENTRIES,
        ),
        "context_recent_entries": _read_int_env(
            "ABRAXAS_CONTEXT_RECENT_ENTRIES",
            DEFAULT_CONTEXT_RECENT_ENTRIES,
        ),
        "auto_braindump_enabled": _read_bool_env(
            "ABRAXAS_AUTO_BRAINDUMP_ENABLED",
            DEFAULT_AUTO_BRAINDUMP_ENABLED,
        ),
        "telegram_stream_mode": os.getenv(
            "ABRAXAS_TELEGRAM_STREAM_MODE",
            DEFAULT_TELEGRAM_STREAM_MODE,
        ),
        "telegram_draft_chunk_min_chars": _read_int_env(
            "ABRAXAS_TELEGRAM_DRAFT_CHUNK_MIN_CHARS",
            DEFAULT_TELEGRAM_DRAFT_CHUNK_MIN_CHARS,
        ),
        "telegram_draft_chunk_max_chars": _read_int_env(
            "ABRAXAS_TELEGRAM_DRAFT_CHUNK_MAX_CHARS",
            DEFAULT_TELEGRAM_DRAFT_CHUNK_MAX_CHARS,
        ),
        "telegram_temp_dir": os.getenv(
            "ABRAXAS_TELEGRAM_TEMP_DIR",
            DEFAULT_TELEGRAM_TEMP_DIR,
        ),
        "telegram_temp_ttl_days": _read_int_env(
            "ABRAXAS_TELEGRAM_TEMP_TTL_DAYS",
            DEFAULT_TELEGRAM_TEMP_TTL_DAYS,
            allow_zero=True,
        ),
        "nous_path": os.getenv("ABRAXAS_NOUS_PATH", DEFAULT_NOUS_PATH),
        "nous_tz": os.getenv("ABRAXAS_NOUS_TZ", DEFAULT_NOUS_TZ),
    }
