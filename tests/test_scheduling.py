from datetime import UTC, datetime

from walt_price_bot.models import ScheduleKind, ScheduleRule
from walt_price_bot.scheduling import schedule_is_due


def schedule(
    *,
    kind: ScheduleKind,
    interval_hours: int | None = None,
    daily_time: str | None = None,
    last_sent_at: str | None = None,
) -> ScheduleRule:
    return ScheduleRule(
        id=1,
        kind=kind,
        interval_hours=interval_hours,
        daily_time=daily_time,
        last_sent_at=last_sent_at,
        enabled=True,
    )


def test_interval_schedule_is_due_after_full_interval() -> None:
    rule = schedule(
        kind=ScheduleKind.INTERVAL,
        interval_hours=3,
        last_sent_at="2026-10-10T05:00:00+00:00",
    )

    assert schedule_is_due(rule, datetime(2026, 10, 10, 7, 59, tzinfo=UTC)) is False
    assert schedule_is_due(rule, datetime(2026, 10, 10, 8, 0, tzinfo=UTC)) is True


def test_daily_schedule_uses_yekaterinburg_calendar_day() -> None:
    rule = schedule(
        kind=ScheduleKind.DAILY,
        daily_time="09:30",
        last_sent_at="2026-10-09T04:30:00+00:00",
    )

    assert schedule_is_due(rule, datetime(2026, 10, 10, 4, 29, tzinfo=UTC)) is False
    assert schedule_is_due(rule, datetime(2026, 10, 10, 4, 30, tzinfo=UTC)) is True
    sent_today = schedule(
        kind=ScheduleKind.DAILY,
        daily_time="09:30",
        last_sent_at="2026-10-10T04:30:00+00:00",
    )
    assert schedule_is_due(sent_today, datetime(2026, 10, 10, 12, 0, tzinfo=UTC)) is False


def test_new_interval_schedule_is_due_immediately() -> None:
    rule = schedule(kind=ScheduleKind.INTERVAL, interval_hours=6)

    assert schedule_is_due(rule, datetime(2026, 10, 10, 8, 0, tzinfo=UTC)) is True
