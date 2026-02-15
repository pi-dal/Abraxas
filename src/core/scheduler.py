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


class MultiDailyScheduler:
    def __init__(self, times_text: str, tz_name: str = DEFAULT_DAILY_SYNC_TZ):
        self.tz_name = tz_name or DEFAULT_DAILY_SYNC_TZ
        self.times = self._parse_times(times_text)
        self._executed_slots: set[str] = set()

    @staticmethod
    def _parse_times(times_text: str) -> list[tuple[int, int]]:
        raw = (times_text or "").strip()
        if not raw:
            return []
        slots: list[tuple[int, int]] = []
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            try:
                hour_text, minute_text = token.split(":", 1)
                hour = int(hour_text)
                minute = int(minute_text)
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    slots.append((hour, minute))
            except Exception:
                continue
        slots = sorted(set(slots))
        return slots

    def now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.tz_name))
        except Exception:
            return datetime.now(ZoneInfo(DEFAULT_DAILY_SYNC_TZ))

    def _prune(self, current: datetime) -> None:
        date_prefix = current.strftime("%Y-%m-%d")
        self._executed_slots = {
            slot_key for slot_key in self._executed_slots if slot_key.startswith(date_prefix)
        }

    def run_if_due(self, job, now: datetime | None = None) -> int:
        current = now or self.now()
        self._prune(current)
        ran = 0
        for hour, minute in self.times:
            if current.hour < hour or (current.hour == hour and current.minute < minute):
                continue
            slot_key = f"{current:%Y-%m-%d} {hour:02d}:{minute:02d}"
            if slot_key in self._executed_slots:
                continue
            self._executed_slots.add(slot_key)
            try:
                job(slot_key)
            except Exception:
                pass
            ran += 1
        return ran


class WeeklyScheduler:
    def __init__(
        self,
        time_text: str = "22:00",
        tz_name: str = DEFAULT_DAILY_SYNC_TZ,
        weekday: int = 6,
    ):
        self.hour, self.minute = DailyScheduler._parse_time(time_text)
        self.tz_name = tz_name or DEFAULT_DAILY_SYNC_TZ
        self.weekday = weekday if 0 <= weekday <= 6 else 6
        self._last_run_week_key: str | None = None

    def now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.tz_name))
        except Exception:
            return datetime.now(ZoneInfo(DEFAULT_DAILY_SYNC_TZ))

    @staticmethod
    def _week_key(current: datetime) -> str:
        iso_year, iso_week, _ = current.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"

    def should_run(self, now: datetime | None = None) -> bool:
        current = now or self.now()
        if current.weekday() != self.weekday:
            return False
        if current.hour < self.hour:
            return False
        if current.hour == self.hour and current.minute < self.minute:
            return False
        if self._last_run_week_key == self._week_key(current):
            return False
        return True

    def run_if_due(self, job, now: datetime | None = None) -> bool:
        current = now or self.now()
        if not self.should_run(current):
            return False
        self._last_run_week_key = self._week_key(current)
        try:
            job()
        except Exception:
            return True
        return True
