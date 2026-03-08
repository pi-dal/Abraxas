"""
Audit Tape Engine - Immutable JSONL logging for all conversation events.

This module provides a physical append-only log that runs parallel to
the in-memory self._messages buffer. The tape survives server restarts,
/handoff operations, and crashes.

File pattern: tapes/session_{session_id}_{YYYYMMDD}.jsonl
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from shutil import move
from threading import Lock
from typing import Any


class TapeEngine:
    """
    Append-only JSONL logger for conversation audit trail.

    Design principles:
    - Write-only: No in-place modifications
    - Fail-safe: Write errors never crash the main flow
    - Thread-safe: Lock-protected concurrent writes
    - Daily rotation: One file per session per day
    """

    def __init__(self, session_id: str, tape_dir: str | None = None):
        """
        Initialize the tape engine.

        Args:
            session_id: Unique identifier for this session (e.g., "tg_123456", "cli_default")
            tape_dir: Root directory for tape files (default: src/memory/tapes)
        """
        self.session_id = session_id
        self._lock = Lock()

        # Determine tape directory
        if tape_dir is None:
            # Default to src/memory/tapes relative to project root
            root = Path(__file__).parent.parent.parent
            tape_dir = root / "src" / "memory" / "tapes"
        else:
            tape_dir = Path(tape_dir)

        self.tape_dir = Path(tape_dir)
        self.tape_dir.mkdir(parents=True, exist_ok=True)

        self._current_date: str | None = None
        self._current_file: Path | None = None
        self._write_count = 0

    def _get_tape_path(self) -> Path:
        """Get the current tape file path for today."""
        today = datetime.now().strftime("%Y%m%d")
        if today != self._current_date:
            self._current_date = today
            filename = f"session_{self.session_id}_{today}.jsonl"
            self._current_file = self.tape_dir / filename
        return self._current_file

    def append(
        self,
        role: str,
        content: str,
        *,
        name: str | None = None,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Append an entry to the tape.

        Args:
            role: Message role ("user", "assistant", "tool", "system")
            content: Message content
            name: Tool name (for tool role)
            tool_call_id: Tool call ID (for tool role)
            metadata: Optional extra fields (e.g., {"source": "telegram"})

        Returns:
            True if write succeeded, False otherwise (silent fail)
        """
        try:
            entry = {
                "ts": datetime.now().isoformat(),
                "role": role,
                "content": content,
            }

            if name:
                entry["name"] = name
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id
            if metadata:
                entry["metadata"] = metadata

            line = json.dumps(entry, ensure_ascii=False)

            with self._lock:
                tape_path = self._get_tape_path()
                with open(tape_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                self._write_count += 1

            return True

        except Exception:
            # Fail silently - never crash the main conversation flow
            return False

    def get_current_path(self) -> str:
        """Get the current tape file path."""
        return str(self._get_tape_path())

    def get_current_size_bytes(self) -> int:
        """Get the current tape file size in bytes."""
        try:
            path = self._get_tape_path()
            if path.exists():
                return path.stat().st_size
        except Exception:
            pass
        return 0

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """
        Read the last n entries from the current tape.

        Args:
            n: Number of entries to retrieve

        Returns:
            List of parsed JSON objects (most recent last)
        """
        try:
            if n <= 0:
                return []
            return self.read_entries(limit=n)
        except Exception:
            return []

    def read_entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        """
        Read tape entries across all session files in chronological order.

        Args:
            limit: Optional maximum number of entries to return from the end

        Returns:
            Parsed JSON objects ordered oldest -> newest.
        """
        try:
            files = sorted(self.list_tape_files(include_archived=True), key=lambda item: item.name)
            if not files:
                path = self._get_tape_path()
                files = [path] if path.exists() else []

            entries: list[dict[str, Any]] = []
            with self._lock:
                for path in files:
                    if not path.exists():
                        continue
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            raw = line.strip()
                            if not raw:
                                continue
                            try:
                                entries.append(json.loads(raw))
                            except json.JSONDecodeError:
                                continue

            if limit is not None and limit >= 0:
                return entries[-limit:]
            return entries
        except Exception:
            return []

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        Search the current tape for lines containing query text.

        Args:
            query: Text to search for (simple substring match)
            limit: Maximum results to return

        Returns:
            List of matching entries (as original JSON lines)
        """
        try:
            query_lower = query.lower()
            matches = []
            for entry in self.read_entries():
                if len(matches) >= limit:
                    break
                raw = json.dumps(entry, ensure_ascii=False)
                if query_lower in raw.lower():
                    matches.append(entry)

            return matches

        except Exception:
            return []

    def list_tape_files(self, *, include_archived: bool = True) -> list[Path]:
        """List all tape files for this session."""
        try:
            patterns = [f"session_{self.session_id}_*.jsonl"]
            if include_archived:
                patterns.append(f"archive_session_{self.session_id}_*.jsonl")
            files: list[Path] = []
            with self._lock:
                for pattern in patterns:
                    files.extend(self.tape_dir.glob(pattern))
            return sorted(
                set(files),
                key=lambda item: (item.stat().st_mtime if item.exists() else 0.0, item.name),
                reverse=True,
            )
        except Exception:
            return []

    def archive_current(self) -> str | None:
        """Move the active tape file into an archive file and continue on a fresh tape."""
        try:
            with self._lock:
                current = self._get_tape_path()
                if not current.exists():
                    return None
                timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
                archived = self.tape_dir / f"archive_session_{self.session_id}_{timestamp}.jsonl"
                move(str(current), str(archived))
                self._current_date = None
                self._current_file = None
                self._write_count = 0
                return str(archived)
        except Exception:
            return None

    def stats(self) -> dict[str, Any]:
        """Get tape statistics."""
        try:
            current_path = self.get_current_path()
            current_size = self.get_current_size_bytes()
            all_files = self.list_tape_files(include_archived=True)
            total_size = sum(f.stat().st_size for f in all_files if f.exists())

            return {
                "session_id": self.session_id,
                "current_file": current_path,
                "current_size_bytes": current_size,
                "current_entries": self._write_count,
                "total_files": len(all_files),
                "total_size_bytes": total_size,
                "tape_dir": str(self.tape_dir),
            }
        except Exception:
            return {
                "error": "Failed to collect stats",
            }
