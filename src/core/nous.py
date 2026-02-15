import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_NOUS_PATH = "src/NOUS.md"
DEFAULT_NOUS_TZ = "Asia/Shanghai"
REINFORCEMENT_SECTION = "NOUS Reinforcements"
HABIT_SECTION = "User Habits (Persistent)"


def resolve_nous_path(nous_path: str | None = None) -> Path:
    raw = nous_path or os.getenv("ABRAXAS_NOUS_PATH", DEFAULT_NOUS_PATH)
    return Path(raw)


def load_nous_text(nous_path: str | None = None) -> str:
    path = resolve_nous_path(nous_path)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def load_nous_prompt(nous_path: str | None = None) -> str:
    text = load_nous_text(nous_path)
    if not text:
        return ""
    return f"NOUS profile loaded:\n{text}"


def write_nous_text(content: str, nous_path: str | None = None) -> str:
    text = content.strip()
    if not text:
        raise ValueError("NOUS content is empty")
    path = resolve_nous_path(nous_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{text}\n", encoding="utf-8")
    return str(path)


def append_nous_text(content: str, nous_path: str | None = None) -> str:
    text = content.strip()
    if not text:
        raise ValueError("NOUS content is empty")
    path = resolve_nous_path(nous_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    with open(path, "a", encoding="utf-8") as file:
        file.write(f"\n{text}\n")
    return str(path)


def _now_text() -> str:
    tz_name = os.getenv("ABRAXAS_NOUS_TZ", DEFAULT_NOUS_TZ)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_NOUS_TZ)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


def _looks_like_habit(text: str) -> bool:
    lowered = text.lower()
    hints = (
        "habit",
        "habits",
        "preference",
        "prefer",
        "usually",
        "always",
        "never",
        "tends to",
        "用户习惯",
        "习惯",
        "偏好",
        "喜欢",
        "不喜欢",
    )
    return any(token in lowered or token in text for token in hints)


def _append_bullet_to_section(text: str, section: str, bullet: str) -> str:
    heading = f"## {section}"
    pattern = re.compile(rf"(?ms)^## {re.escape(section)}\n(.*?)(?=^## |\Z)")
    match = pattern.search(text)
    if not match:
        base = text.rstrip()
        delimiter = "\n\n---\n\n" if base else ""
        return f"{base}{delimiter}{heading}\n- {bullet}\n"

    body = match.group(1).rstrip()
    new_body = f"{body}\n- {bullet}\n\n" if body else f"- {bullet}\n\n"
    return f"{text[:match.start(1)]}{new_body}{text[match.end(1):]}"


def reinforce_nous_from_dialogue(
    dialogue_text: str,
    nous_path: str | None = None,
    *,
    force_habit: bool = False,
) -> tuple[str, str]:
    text = dialogue_text.strip()
    if not text:
        raise ValueError("NOUS reinforcement text is empty")
    path = resolve_nous_path(nous_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    timestamped = f"[{_now_text()}] {text}"
    section = HABIT_SECTION if force_habit or _looks_like_habit(text) else REINFORCEMENT_SECTION
    updated = _append_bullet_to_section(existing, section, timestamped)
    path.write_text(updated.rstrip() + "\n", encoding="utf-8")
    return str(path), section
