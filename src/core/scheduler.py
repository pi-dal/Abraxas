from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_DAILY_SYNC_TIME = "02:00"
DEFAULT_DAILY_SYNC_TZ = "Asia/Shanghai"


class DailyScheduler:
    def __init__(self, time_text: str = DEFAULT_DAILY_SYNC_TIME, tz_name: str = DEFAULT_DAILY_SYNC_TZ):
        self.hour, self.minute = self._parse_time(time_text)
        self.tz_name = tz_name or DEFAULT_DAILY_SYNC_TZ
        self._last_run_date: date | None = None

    @staticmethod
    def _parse_time(time_text: str) -> tuple[int, int]:
        raw = (time_text or "").strip()
        try:
            hour_text, minute_text = raw.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except Exception:
            pass
        return 2, 0

    def now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.tz_name))
        except Exception:
            return datetime.now(ZoneInfo(DEFAULT_DAILY_SYNC_TZ))

    def should_run(self, now: datetime | None = None) -> bool:
        current = now or self.now()
        if self._last_run_date == current.date():
            return False
        if current.hour < self.hour:
            return False
        if current.hour == self.hour and current.minute < self.minute:
            return False
        return True

    def run_if_due(self, job) -> bool:
        current = self.now()
        if not self.should_run(current):
            return False
        self._last_run_date = current.date()
        try:
            job()
        except Exception:
            return True
        return True
