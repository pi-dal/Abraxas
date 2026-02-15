import subprocess
import re
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
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

MISSION_MEMORY_START = "<!-- mission-memory:start -->"
MISSION_MEMORY_END = "<!-- mission-memory:end -->"
WEEKLY_COMPOUND_START = "<!-- weekly-compound:start -->"
WEEKLY_COMPOUND_END = "<!-- weekly-compound:end -->"


@dataclass
class MemoryRuntime:
    memory_dir: Path
    qmd_command: str
    top_k: int
    max_inject_chars: int
    qmd_timeout_sec: int
    tz_name: str
    last_qmd_error: str = ""

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

    def _run_qmd(self, command: list[str]) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.qmd_timeout_sec,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except Exception as exc:
            return 1, "", str(exc)

    def _set_qmd_error(self, message: str) -> None:
        self.last_qmd_error = message.strip()

    def _clear_qmd_error(self) -> None:
        self.last_qmd_error = ""

    def qmd_status(self) -> str:
        return self.last_qmd_error or "ok"

    def memory_status(self) -> str:
        daily_dir = self.memory_dir / "daily"
        daily_files = sorted(daily_dir.glob("*.md")) if daily_dir.exists() else []
        last_daily = daily_files[-1].name if daily_files else "(none)"
        lines = [
            "memory status:",
            f"- dir: {self.memory_dir}",
            f"- memory_file: {'yes' if self.memory_file.exists() else 'no'}",
            f"- braindump_file: {'yes' if self.braindump_file.exists() else 'no'}",
            f"- mission_log_file: {'yes' if self.mission_log_file.exists() else 'no'}",
            f"- daily_logs: {len(daily_files)} (last: {last_daily})",
            f"- qmd_status: {self.qmd_status()}",
        ]
        return "\n".join(lines)

    def doctor_report(self) -> str:
        daily_dir = self.memory_dir / "daily"
        daily_files = sorted(daily_dir.glob("*.md")) if daily_dir.exists() else []
        version_code, version_stdout, version_stderr = self._run_qmd([self.qmd_command, "--version"])
        qmd_available = version_code == 0
        qmd_version = version_stdout.strip() if qmd_available else (version_stderr.strip() or "unknown")

        lines = [
            "memory doctor:",
            f"- memory_dir: {self.memory_dir}",
            f"- qmd_command: {self.qmd_command}",
            f"- qmd_available: {'yes' if qmd_available else 'no'}",
            f"- qmd_version: {qmd_version}",
            f"- qmd_status: {self.qmd_status()}",
            f"- memory_file: {'yes' if self.memory_file.exists() else 'no'}",
            f"- braindump_file: {'yes' if self.braindump_file.exists() else 'no'}",
            f"- mission_log_file: {'yes' if self.mission_log_file.exists() else 'no'}",
            f"- daily_logs: {len(daily_files)}",
        ]
        suggestions: list[str] = []
        if not qmd_available:
            suggestions.append("install qmd and make sure ABRAXAS_QMD_COMMAND is executable")
        if not self.memory_file.exists():
            suggestions.append("create src/memory/MEMORY.md or run /memory sync once")
        if not self.braindump_file.exists():
            suggestions.append("capture a note with /remember to initialize braindump")
        if not daily_files:
            suggestions.append("run /memory sync to produce first daily log")

        if suggestions:
            lines.append("suggestion:")
            lines.extend(f"- {item}" for item in suggestions)
        else:
            lines.append("suggestion:")
            lines.append("- memory pipeline looks healthy")
        return "\n".join(lines)

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

    @staticmethod
    def _extract_braindump_body(line: str) -> str:
        clean = line.strip()
        if not clean.startswith("- "):
            return ""
        body = clean[2:].strip()
        match = re.match(r"\[[^\]]+\]\s+\[[^\]]*\]\s+(.*)$", body)
        if match:
            body = match.group(1).strip()
        return body

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(text.strip().split()).lower()

    @staticmethod
    def _upsert_marked_block(
        source: str,
        start_marker: str,
        end_marker: str,
        block_body: str,
    ) -> tuple[str, bool]:
        wrapped = f"{start_marker}\n{block_body.rstrip()}\n{end_marker}"
        pattern = re.compile(
            re.escape(start_marker) + r".*?" + re.escape(end_marker),
            flags=re.DOTALL,
        )
        source_clean = source.strip()
        if pattern.search(source_clean):
            updated = pattern.sub(wrapped, source_clean)
        elif source_clean:
            updated = f"{source_clean}\n\n{wrapped}"
        else:
            updated = wrapped
        updated = updated.rstrip() + "\n"
        changed = updated != (source if source.endswith("\n") else source + ("\n" if source else ""))
        return updated, changed

    def promote_braindump_to_mission(self, limit: int = 20) -> str:
        if limit <= 0:
            limit = 20
        if not self.braindump_file.exists():
            return "mission sync skipped: no braindump"

        try:
            content = self.braindump_file.read_text(encoding="utf-8")
        except Exception:
            return "mission sync failed: cannot read braindump"

        entries: list[str] = []
        for line in content.splitlines():
            body = self._extract_braindump_body(line)
            if body:
                entries.append(body)
        if not entries:
            return "mission sync skipped: no entries"

        existing_ids: set[str] = set()
        if self.mission_log_file.exists():
            try:
                mission_content = self.mission_log_file.read_text(encoding="utf-8")
            except Exception:
                mission_content = ""
            for match in re.findall(r"\[braindump:([0-9a-f]{12})\]", mission_content):
                existing_ids.add(match)

        pending: list[tuple[str, str]] = []
        seen_pending: set[str] = set()
        for body in entries:
            normalized = self._normalize_text(body)
            if not normalized:
                continue
            digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
            if digest in existing_ids or digest in seen_pending:
                continue
            seen_pending.add(digest)
            pending.append((digest, body))
        if not pending:
            return "mission sync skipped: up-to-date"

        selected = pending[-limit:]
        timestamp = self._now().strftime("%Y-%m-%d %H:%M")
        if not self.mission_log_file.exists():
            self.mission_log_file.write_text("# Mission Log\n\n", encoding="utf-8")
        with open(self.mission_log_file, "a", encoding="utf-8") as file:
            for digest, body in selected:
                file.write(f"- [{timestamp} {self.tz_name}] [braindump:{digest}] {body}\n")
        return f"mission sync saved: {len(selected)} item(s)"

    @staticmethod
    def _extract_mission_body(line: str) -> str:
        clean = line.strip()
        if not clean.startswith("- "):
            return ""
        body = clean[2:].strip()
        match = re.match(r"\[[^\]]+\]\s+(?:\[braindump:[0-9a-f]{12}\]\s+)?(.*)$", body)
        if match:
            body = match.group(1).strip()
        return body

    def sync_mission_to_memory(self, limit: int = 30) -> str:
        if limit <= 0:
            limit = 30
        if not self.mission_log_file.exists():
            return "mission memory sync skipped: no mission log"
        try:
            content = self.mission_log_file.read_text(encoding="utf-8")
        except Exception:
            return "mission memory sync failed: cannot read mission log"

        extracted: list[str] = []
        for line in content.splitlines():
            body = self._extract_mission_body(line)
            if body:
                extracted.append(body)
        if not extracted:
            return "mission memory sync skipped: no mission entries"

        unique_latest: list[str] = []
        seen: set[str] = set()
        for body in reversed(extracted):
            normalized = self._normalize_text(body)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_latest.append(body)
            if len(unique_latest) >= limit:
                break
        unique_latest.reverse()
        if not unique_latest:
            return "mission memory sync skipped: up-to-date"

        now = self._now()
        section_lines = [f"## Mission Memory {now:%Y-%m-%d}"] + [f"- {item}" for item in unique_latest]
        block = "\n".join(section_lines)
        existing = ""
        if self.memory_file.exists():
            try:
                existing = self.memory_file.read_text(encoding="utf-8")
            except Exception:
                existing = ""
        updated, changed = self._upsert_marked_block(
            existing,
            MISSION_MEMORY_START,
            MISSION_MEMORY_END,
            block,
        )
        if not changed:
            return "mission memory sync skipped: up-to-date"
        self.memory_file.write_text(updated, encoding="utf-8")
        return f"mission memory sync saved: {len(unique_latest)} item(s)"

    def query(self, query_text: str) -> str:
        q = query_text.strip()
        if not q:
            return ""

        query_cmd = [self.qmd_command, "query", q, "--top-k", str(self.top_k)]
        code, stdout, stderr = self._run_qmd(query_cmd)
        if code != 0:
            fallback_cmd = [self.qmd_command, "query", q]
            code, stdout, stderr = self._run_qmd(fallback_cmd)
        if code != 0:
            self._set_qmd_error(f"qmd query failed: {stderr.strip() or 'unknown error'}")
            return ""

        output = stdout.strip()
        if not output:
            self._set_qmd_error("qmd query returned empty output")
            return ""

        refs = self._extract_refs(output)
        if refs:
            snippets = self._fetch_snippets(refs[: self.top_k])
            if snippets:
                merged = "\n\n".join(snippets)
                self._clear_qmd_error()
                return merged[: self.max_inject_chars]

        self._clear_qmd_error()
        return output[: self.max_inject_chars]

    @staticmethod
    def _extract_refs(output: str) -> list[str]:
        pattern = re.compile(r"([^\s:]+\.md:\d+)")
        refs: list[str] = []
        for match in pattern.findall(output):
            if match not in refs:
                refs.append(match)
        return refs

    def _fetch_snippets(self, refs: list[str]) -> list[str]:
        snippets: list[str] = []
        for ref in refs:
            code, stdout, stderr = self._run_qmd([self.qmd_command, "get", ref, "-l", "20"])
            if code != 0:
                self._set_qmd_error(f"qmd get failed for {ref}: {stderr.strip() or 'unknown error'}")
                continue
            text = stdout.strip()
            if not text:
                continue
            snippets.append(text)
        return snippets

    def refresh_index(self) -> str:
        update_code, _, update_err = self._run_qmd([self.qmd_command, "update"])
        if update_code != 0:
            message = f"memory index refresh failed: qmd update ({update_err.strip() or 'unknown error'})"
            self._set_qmd_error(message)
            return message
        embed_code, _, embed_err = self._run_qmd([self.qmd_command, "embed"])
        if embed_code != 0:
            message = f"memory index refresh failed: qmd embed ({embed_err.strip() or 'unknown error'})"
            self._set_qmd_error(message)
            return message
        self._clear_qmd_error()
        return "memory index refreshed"

    def compound_weekly_memory(self, days: int = 7) -> str:
        if days <= 0:
            days = 7
        now = self._now()
        candidates: list[Path] = []
        for offset in range(days):
            day = now - timedelta(days=offset)
            path = self._daily_file_for(day)
            if path.exists():
                candidates.append(path)
        if not candidates:
            return "memory weekly compound skipped: no daily logs"

        points: list[str] = []
        for path in sorted(candidates):
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for line in content.splitlines():
                clean = line.strip()
                if clean.startswith("- "):
                    points.append(clean)
            if len(points) >= 40:
                break
        if not points:
            return "memory weekly compound skipped: no extractable points"

        section = [f"## Weekly Compound {now:%Y-%m-%d}"] + points[:40]
        block = "\n".join(section)
        existing = ""
        if self.memory_file.exists():
            try:
                existing = self.memory_file.read_text(encoding="utf-8")
            except Exception:
                existing = ""
        updated, _ = self._upsert_marked_block(
            existing,
            WEEKLY_COMPOUND_START,
            WEEKLY_COMPOUND_END,
            block,
        )
        self.memory_file.write_text(updated, encoding="utf-8")
        return "memory weekly compound saved"


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
