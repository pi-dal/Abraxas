"""
RCI (Recursive Critique and Improvement) session state management.

Provides per-conversation strict mode tracking for enhanced execution protocol.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class RCISessionState:
    """Per-conversation RCI state tracker."""

    strict_mode_enabled: bool = False
    strict_mode_expires_at: Optional[datetime] = None

    def enable_strict_mode(self, duration_minutes: int = 30) -> None:
        """Enable strict RCI mode with optional expiration."""
        self.strict_mode_enabled = True
        self.strict_mode_expires_at = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)

    def disable_strict_mode(self) -> None:
        """Disable strict RCI mode."""
        self.strict_mode_enabled = False
        self.strict_mode_expires_at = None

    def is_strict_mode_active(self) -> bool:
        """Check if strict mode is currently active (auto-expires)."""
        if not self.strict_mode_enabled:
            return False

        # Auto-expire if time has passed
        if self.strict_mode_expires_at and datetime.now(timezone.utc) >= self.strict_mode_expires_at:
            self.strict_mode_enabled = False
            self.strict_mode_expires_at = None
            return False

        return True

    def get_remaining_minutes(self) -> Optional[int]:
        """Get remaining minutes until strict mode expires, or None if not active."""
        if not self.is_strict_mode_active() or not self.strict_mode_expires_at:
            return None

        remaining = self.strict_mode_expires_at - datetime.now(timezone.utc)
        return max(0, int(remaining.total_seconds() // 60))

    def get_status_summary(self) -> str:
        """Get human-readable status summary."""
        if not self.is_strict_mode_active():
            return "RCI: standard mode"

        remaining = self.get_remaining_minutes()
        if remaining is None:
            return "RCI: strict mode (indefinite)"

        return f"RCI: strict mode ({remaining} min remaining)"
