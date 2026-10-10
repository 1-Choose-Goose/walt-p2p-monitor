from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import ScheduleKind, ScheduleRule

YEKT = ZoneInfo("Asia/Yekaterinburg")


def next_schedule_due(
    rule: ScheduleRule, now: datetime, *, timezone: ZoneInfo = YEKT
) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if rule.kind is ScheduleKind.INTERVAL:
        if not rule.interval_hours or rule.interval_hours <= 0:
            raise ValueError("positive interval required")
        if rule.last_sent_at is None:
            return now
        return _parse_aware(rule.last_sent_at) + timedelta(hours=rule.interval_hours)

    if rule.kind is ScheduleKind.DAILY:
        if rule.daily_time is None:
            raise ValueError("daily time required")
        hour, minute = _parse_clock(rule.daily_time)
        local_now = now.astimezone(timezone)
        today_due = datetime.combine(local_now.date(), time(hour, minute), tzinfo=timezone)
        if rule.last_sent_at is None:
            return today_due.astimezone(UTC)
        last_local = _parse_aware(rule.last_sent_at).astimezone(timezone)
        if last_local.date() >= local_now.date():
            return (today_due + timedelta(days=1)).astimezone(UTC)
        return today_due.astimezone(UTC)

    raise ValueError("unsupported schedule kind")


def schedule_is_due(
    rule: ScheduleRule, now: datetime, *, timezone: ZoneInfo = YEKT
) -> bool:
    if not rule.enabled:
        return False
    return now.astimezone(UTC) >= next_schedule_due(
        rule, now, timezone=timezone
    ).astimezone(UTC)


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("stored datetime must be timezone-aware")
    return parsed


def _parse_clock(value: str) -> tuple[int, int]:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid daily time") from exc
    return parsed.hour, parsed.minute
