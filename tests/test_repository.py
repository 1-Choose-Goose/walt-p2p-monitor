import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from walt_price_bot.models import (
    AlertKind,
    Asset,
    OpportunityMode,
    QuoteSide,
    ScheduleKind,
    ThresholdDirection,
    UserRole,
)
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, Repository


@pytest.mark.asyncio
async def test_owner_is_fixed_administrator(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    results = await asyncio.gather(
        repository.claim_owner(101),
        repository.claim_owner(ADMIN_TELEGRAM_ID),
    )

    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_initialize_repairs_fixed_administrator_role(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    repository = Repository(path)
    await repository.initialize()
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE users SET role = 'USER' WHERE telegram_id = ?",
            (ADMIN_TELEGRAM_ID,),
        )

    await repository.initialize()

    administrator = await repository.get_user(ADMIN_TELEGRAM_ID)
    assert administrator is not None
    assert administrator.role is UserRole.ADMIN
    assert await repository.get_owner_id() == ADMIN_TELEGRAM_ID


@pytest.mark.asyncio
async def test_repository_persists_pause_and_alerts(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()
    await repository.set_paused(ADMIN_TELEGRAM_ID, True)
    alert_id = await repository.add_alert(
        ADMIN_TELEGRAM_ID,
        kind=AlertKind.THRESHOLD,
        asset=Asset.USDT,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="90.50",
    )

    reopened = Repository(database)
    await reopened.initialize()

    assert await reopened.is_paused(ADMIN_TELEGRAM_ID) is True
    alerts = await reopened.list_alerts(ADMIN_TELEGRAM_ID)
    assert [alert.id for alert in alerts] == [alert_id]
    assert alerts[0].target == "90.50"


@pytest.mark.asyncio
async def test_repository_persists_last_bot_message_for_chat_cleanup(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()

    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 321)

    reopened = Repository(database)
    await reopened.initialize()
    assert await reopened.get_last_message_id(ADMIN_TELEGRAM_ID) == 321


@pytest.mark.asyncio
async def test_repository_accepts_bitcoin_alerts(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    alert_id = await repository.add_alert(
        ADMIN_TELEGRAM_ID,
        kind=AlertKind.THRESHOLD,
        asset=Asset.BTC,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="7000000",
    )

    assert (await repository.list_alerts(ADMIN_TELEGRAM_ID))[0].id == alert_id


@pytest.mark.asyncio
async def test_repository_migrates_existing_alert_table_for_bitcoin(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('threshold', 'percent')),
                asset TEXT NOT NULL CHECK (asset IN ('USDT', 'TON')),
                quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
                threshold_direction TEXT CHECK (threshold_direction IN ('above', 'below')),
                target TEXT NOT NULL,
                baseline TEXT,
                armed INTEGER NOT NULL DEFAULT 1 CHECK (armed IN (0, 1)),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
            )
            """
        )
        db.execute(
            """
            INSERT INTO alerts
                (kind, asset, quote_side, threshold_direction, target)
            VALUES ('threshold', 'USDT', 'BUY', 'below', '90')
            """
        )

    repository = Repository(database)
    await repository.initialize()
    await repository.add_alert(
        ADMIN_TELEGRAM_ID,
        kind=AlertKind.THRESHOLD,
        asset=Asset.BTC,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="7000000",
    )

    assert [
        alert.asset for alert in await repository.list_alerts(ADMIN_TELEGRAM_ID)
    ] == [Asset.USDT, Asset.BTC]


@pytest.mark.asyncio
async def test_opportunity_notifications_are_opt_in_and_persist_settings(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()

    defaults = await repository.get_opportunity_settings(ADMIN_TELEGRAM_ID)
    assert defaults.enabled is False
    assert defaults.mode is OpportunityMode.BOTH
    assert defaults.min_spread_percent == Decimal("2")

    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1.5"),
    )

    reopened = Repository(database)
    await reopened.initialize()
    configured = await reopened.get_opportunity_settings(ADMIN_TELEGRAM_ID)
    assert configured.enabled is True
    assert configured.mode is OpportunityMode.BUY
    assert configured.min_spread_percent == Decimal("1.5")

    await reopened.disable_opportunities(ADMIN_TELEGRAM_ID)
    assert (await reopened.get_opportunity_settings(ADMIN_TELEGRAM_ID)).enabled is False


@pytest.mark.asyncio
async def test_opportunity_deduplication_is_per_asset_and_side(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    assert await repository.get_last_opportunity_ad(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY
    ) is None


@pytest.mark.asyncio
async def test_opportunity_history_persists_all_ads_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()
    await repository.mark_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-a"
    )
    await repository.mark_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-b"
    )

    reopened = Repository(database)
    await reopened.initialize()

    assert await reopened.was_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-a"
    ) is True
    assert await reopened.was_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-b"
    ) is True


@pytest.mark.asyncio
async def test_changing_opportunity_settings_preserves_active_history(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    now = datetime.now(UTC)
    await repository.mark_opportunities_notified(
        ADMIN_TELEGRAM_ID,
        [(Asset.USDT, QuoteSide.BUY, "offer-a", "Лот")],
        now,
    )

    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.SELL,
        min_spread_percent=Decimal("3"),
        max_buy_premium_percent=Decimal("1"),
    )

    assert await repository.was_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-a", now
    )
    assert await repository.count_opportunity_history(ADMIN_TELEGRAM_ID, now) == 1


@pytest.mark.asyncio
async def test_visible_opportunity_history_is_deleted_after_ten_minutes(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    created_at = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)

    await repository.save_opportunity_history(
        ADMIN_TELEGRAM_ID,
        Asset.USDT,
        QuoteSide.BUY,
        "offer-a",
        "Выгодный лот USDT",
        created_at,
    )

    visible = await repository.list_opportunity_history(
        ADMIN_TELEGRAM_ID, created_at + timedelta(minutes=9, seconds=59)
    )
    assert [entry.message_text for entry in visible] == ["Выгодный лот USDT"]

    expired = await repository.list_opportunity_history(
        ADMIN_TELEGRAM_ID, created_at + timedelta(minutes=10)
    )
    assert expired == []
    assert await repository.prune_opportunity_history(
        created_at + timedelta(minutes=10)
    ) == 1

    async with repository._connect() as db:
        count = await repository._scalar(
            db,
            "SELECT COUNT(*) FROM opportunity_notification_history WHERE telegram_id = ?",
            (ADMIN_TELEGRAM_ID,),
        )
    assert count == 0


@pytest.mark.asyncio
async def test_opportunity_history_uses_non_reusable_ids_and_pagination(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    created_at = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)

    for index in range(50):
        await repository.save_opportunity_history(
            ADMIN_TELEGRAM_ID,
            Asset.USDT,
            QuoteSide.BUY,
            f"offer-{index}",
            f"Лот {index}",
            created_at + timedelta(seconds=index),
        )

    pages = [
        await repository.list_opportunity_history(
            ADMIN_TELEGRAM_ID,
            created_at + timedelta(minutes=1),
            limit=15,
            offset=offset,
        )
        for offset in (0, 15, 30, 45)
    ]
    assert [len(page) for page in pages] == [15, 15, 15, 5]
    assert [entry.message_text for page in pages for entry in page] == [
        f"Лот {index}" for index in range(49, -1, -1)
    ]
    assert await repository.count_opportunity_history(
        ADMIN_TELEGRAM_ID, created_at + timedelta(minutes=1)
    ) == 50

    deleted_id = pages[-1][-1].id
    async with repository._connect() as db:
        await db.execute(
            "DELETE FROM opportunity_notification_history WHERE id = ?", (deleted_id,)
        )
        await db.commit()
    await repository.save_opportunity_history(
        ADMIN_TELEGRAM_ID,
        Asset.BTC,
        QuoteSide.SELL,
        "replacement",
        "Новый лот",
        created_at + timedelta(minutes=2),
    )
    newest = (
        await repository.list_opportunity_history(
            ADMIN_TELEGRAM_ID, created_at + timedelta(minutes=2), limit=1
        )
    )[0]
    assert newest.id > deleted_id


@pytest.mark.asyncio
async def test_recent_opportunity_keys_are_isolated_by_user(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    now = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)
    await repository.mark_opportunities_notified(
        ADMIN_TELEGRAM_ID,
        [(Asset.USDT, QuoteSide.BUY, "offer-a", "Лот")],
        now,
    )

    assert await repository.get_recent_opportunity_keys(ADMIN_TELEGRAM_ID, now) == {
        (Asset.USDT, QuoteSide.BUY, "offer-a")
    }
    assert await repository.get_recent_opportunity_keys(999, now) == set()
    assert await repository.get_recent_opportunity_keys(
        ADMIN_TELEGRAM_ID, now + timedelta(minutes=10)
    ) == set()


@pytest.mark.asyncio
async def test_configured_owner_conflict_is_not_silent(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    with pytest.raises(RuntimeError, match="owner"):
        await repository.set_configured_owner(202)


@pytest.mark.asyncio
async def test_old_database_migration_preserves_user_data(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE settings (
                singleton INTEGER PRIMARY KEY,
                owner_id INTEGER,
                paused INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO settings VALUES (1, 101, 1);
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('threshold', 'percent')),
                asset TEXT NOT NULL CHECK (asset IN ('USDT', 'TON')),
                quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
                threshold_direction TEXT CHECK (threshold_direction IN ('above', 'below')),
                target TEXT NOT NULL,
                baseline TEXT,
                armed INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO alerts
                (kind, asset, quote_side, threshold_direction, target)
            VALUES ('threshold', 'USDT', 'BUY', 'below', '90');
            CREATE TABLE schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                interval_hours INTEGER,
                daily_time TEXT,
                last_sent_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO schedules(kind, interval_hours, last_sent_at)
            VALUES ('interval', 6, '2026-10-10T08:00:00+00:00');
            CREATE TABLE opportunity_notifications (
                asset TEXT NOT NULL,
                quote_side TEXT NOT NULL,
                last_ad_id TEXT NOT NULL,
                PRIMARY KEY (asset, quote_side)
            );
            INSERT INTO opportunity_notifications VALUES ('USDT', 'BUY', 'offer-a');
            """
        )

    repository = Repository(database)
    await repository.initialize()
    await repository.initialize()

    assert await repository.get_owner_id() == ADMIN_TELEGRAM_ID
    assert await repository.is_paused(ADMIN_TELEGRAM_ID) is True
    assert [rule.target for rule in await repository.list_alerts(ADMIN_TELEGRAM_ID)] == [
        "90"
    ]
    assert (await repository.list_schedules(ADMIN_TELEGRAM_ID))[0].last_sent_at == (
        "2026-10-10T08:00:00+00:00"
    )
    assert not await repository.was_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-a"
    )


@pytest.mark.asyncio
async def test_schedule_state_persists_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()
    schedule_id = await repository.add_schedule(
        ADMIN_TELEGRAM_ID,
        kind=ScheduleKind.DAILY, daily_time="09:30"
    )
    await repository.mark_schedule_sent(
        ADMIN_TELEGRAM_ID, schedule_id, "2026-10-10T04:30:00+00:00"
    )

    reopened = Repository(database)
    await reopened.initialize()

    schedule = (await reopened.list_schedules(ADMIN_TELEGRAM_ID))[0]
    assert schedule.daily_time == "09:30"
    assert schedule.last_sent_at == "2026-10-10T04:30:00+00:00"
    await repository.mark_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY, "offer-1"
    )
    await repository.mark_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.BTC, QuoteSide.SELL, "offer-2"
    )

    assert await repository.get_last_opportunity_ad(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY
    ) == "offer-1"
    assert await repository.get_last_opportunity_ad(
        ADMIN_TELEGRAM_ID, Asset.BTC, QuoteSide.SELL
    ) == "offer-2"
    assert await repository.get_last_opportunity_ad(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.SELL
    ) is None

    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BOTH,
        min_spread_percent=Decimal("2"),
    )
    assert await repository.get_last_opportunity_ad(
        ADMIN_TELEGRAM_ID, Asset.USDT, QuoteSide.BUY
    ) is None
