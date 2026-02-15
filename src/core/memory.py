import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .settings import (
    DEFAULT_MEMORY_DIR,
    DEFAULT_MEMORY_MAX_INJECT_CHARS,
    DEFAULT_MEMORY_TOP_K,
    DEFAULT_MEMORY_TZ,
    DEFAULT_QMD_COMMAND,
    DEFAULT_QMD_TIMEOUT_SEC,
    load_runtime_settings,
)


@dataclass
class MemoryRuntime:
    memory_dir: Path
    qmd_command: str
    top_k: int
    max_inject_chars: int
    qmd_timeout_sec: int
    tz_name: str

    def __post_init__(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "daily").mkdir(parents=True, exist_ok=True)

    @property
    def memory_file(self) -> Path:
        return self.memory_dir / "MEMORY.md"

    @property
    def braindump_file(self) -> Path:
        return self.memory_dir / "braindump.md"

    @property
    def mission_log_file(self) -> Path:
        return self.memory_dir / "mission-log.md"

    def _now(self) -> datetime:
        try:
            tz = ZoneInfo(self.tz_name)
        except Exception:
            tz = ZoneInfo(DEFAULT_MEMORY_TZ)
        return datetime.now(tz)

    def _daily_file_for(self, when: datetime | None = None) -> Path:
        current = when or self._now()
        return self.memory_dir / "daily" / f"{current:%Y-%m-%d}.md"

    def load_memory_brief(self) -> str:
        if not self.memory_file.exists():
            return ""
        try:
            return self.memory_file.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def append_braindump(self, note: str, tags: list[str] | None = None) -> str:
        text = note.strip()
        if not text:
            return "memory error: note is empty"
        clean_tags = [tag.strip() for tag in (tags or []) if tag.strip()]
        tag_text = ",".join(clean_tags)
        timestamp = self._now().strftime("%Y-%m-%d %H:%M")
        line = f"- [{timestamp} {self.tz_name}] [{tag_text}] {text}\n"
        if not self.braindump_file.exists():
            header = "# Braindump\n\nAppend-only idea inbox.\n\n"
            self.braindump_file.write_text(header, encoding="utf-8")
        with open(self.braindump_file, "a", encoding="utf-8") as file:
            file.write(line)
        return "memory saved to braindump"

    def append_daily_entry(self, text: str, *, section: str = "Notes") -> str:
        body = text.strip()
        if not body:
            return "memory skipped: empty daily entry"
        daily_file = self._daily_file_for()
        if not daily_file.exists():
            daily_file.write_text(
                f"# {daily_file.stem} Daily Log\n\n",
                encoding="utf-8",
            )
        with open(daily_file, "a", encoding="utf-8") as file:
            file.write(f"\n## {section}\n{body}\n")
        return f"memory saved to {daily_file.name}"

    def record_compaction(self, summary: str) -> str:
        return self.append_daily_entry(summary, section="Compaction")

    def record_daily_sync(self, summary: str) -> str:
        return self.append_daily_entry(summary, section="Daily Sync")

    def record_mission_log(self, text: str) -> str:
        body = text.strip()
        if not body:
            return "memory skipped: empty mission log"
        if not self.mission_log_file.exists():
            self.mission_log_file.write_text("# Mission Log\n\n", encoding="utf-8")
        timestamp = self._now().strftime("%Y-%m-%d %H:%M")
        with open(self.mission_log_file, "a", encoding="utf-8") as file:
            file.write(f"- [{timestamp} {self.tz_name}] {body}\n")
        return "memory saved to mission log"

    def query(self, query_text: str) -> str:
        q = query_text.strip()
        if not q:
            return ""
        try:
            proc = subprocess.run(
                [self.qmd_command, "query", q],
                capture_output=True,
                text=True,
                timeout=self.qmd_timeout_sec,
            )
        except Exception:
            return ""
        if proc.returncode != 0:
            return ""
        output = (proc.stdout or "").strip()
        if not output:
            return ""
        return output[: self.max_inject_chars]

    def refresh_index(self) -> str:
        try:
            update = subprocess.run(
                [self.qmd_command, "update"],
                capture_output=True,
                text=True,
                timeout=self.qmd_timeout_sec,
            )
            if update.returncode != 0:
                return "memory index refresh failed: qmd update"
            embed = subprocess.run(
                [self.qmd_command, "embed"],
                capture_output=True,
                text=True,
                timeout=self.qmd_timeout_sec,
            )
            if embed.returncode != 0:
                return "memory index refresh failed: qmd embed"
            return "memory index refreshed"
        except Exception:
            return "memory index refresh failed"


def create_memory_runtime(
    memory_dir: str | None = None,
    *,
    settings: dict[str, Any] | None = None,
) -> MemoryRuntime:
    runtime_settings = settings or load_runtime_settings()
    runtime_dir = memory_dir or str(runtime_settings.get("memory_dir", DEFAULT_MEMORY_DIR))
    qmd_command = str(runtime_settings.get("qmd_command", DEFAULT_QMD_COMMAND))
    top_k = int(runtime_settings.get("memory_top_k", DEFAULT_MEMORY_TOP_K))
    max_chars = int(runtime_settings.get("memory_max_inject_chars", DEFAULT_MEMORY_MAX_INJECT_CHARS))
    qmd_timeout_sec = int(runtime_settings.get("qmd_timeout_sec", DEFAULT_QMD_TIMEOUT_SEC))
    tz_name = str(runtime_settings.get("memory_tz", DEFAULT_MEMORY_TZ))
    return MemoryRuntime(
        memory_dir=Path(runtime_dir),
        qmd_command=qmd_command,
        top_k=top_k,
        max_inject_chars=max_chars,
        qmd_timeout_sec=qmd_timeout_sec,
        tz_name=tz_name,
    )
