import asyncio
import sqlite3
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
)
from walt_price_bot.repository import Repository


@pytest.mark.asyncio
async def test_first_owner_claim_is_atomic(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    results = await asyncio.gather(
        repository.claim_owner(101),
        repository.claim_owner(202),
    )

    assert sorted(results) == [False, True]
    assert await repository.get_owner_id() in {101, 202}


@pytest.mark.asyncio
async def test_repository_persists_pause_and_alerts(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()
    await repository.claim_owner(101)
    await repository.set_paused(True)
    alert_id = await repository.add_alert(
        kind=AlertKind.THRESHOLD,
        asset=Asset.USDT,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="90.50",
    )

    reopened = Repository(database)
    await reopened.initialize()

    assert await reopened.is_paused() is True
    alerts = await reopened.list_alerts()
    assert [alert.id for alert in alerts] == [alert_id]
    assert alerts[0].target == "90.50"


@pytest.mark.asyncio
async def test_repository_persists_last_bot_message_for_chat_cleanup(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()

    await repository.set_last_message_id(321)

    reopened = Repository(database)
    await reopened.initialize()
    assert await reopened.get_last_message_id() == 321


@pytest.mark.asyncio
async def test_repository_accepts_bitcoin_alerts(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    alert_id = await repository.add_alert(
        kind=AlertKind.THRESHOLD,
        asset=Asset.BTC,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="7000000",
    )

    assert (await repository.list_alerts())[0].id == alert_id


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
        kind=AlertKind.THRESHOLD,
        asset=Asset.BTC,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="7000000",
    )

    assert [alert.asset for alert in await repository.list_alerts()] == [Asset.USDT, Asset.BTC]


@pytest.mark.asyncio
async def test_opportunity_notifications_are_opt_in_and_persist_settings(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()

    defaults = await repository.get_opportunity_settings()
    assert defaults.enabled is False
    assert defaults.mode is OpportunityMode.BOTH
    assert defaults.min_spread_percent == Decimal("2")

    await repository.configure_opportunities(
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1.5"),
    )

    reopened = Repository(database)
    await reopened.initialize()
    configured = await reopened.get_opportunity_settings()
    assert configured.enabled is True
    assert configured.mode is OpportunityMode.BUY
    assert configured.min_spread_percent == Decimal("1.5")

    await reopened.disable_opportunities()
    assert (await reopened.get_opportunity_settings()).enabled is False


@pytest.mark.asyncio
async def test_opportunity_deduplication_is_per_asset_and_side(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    assert await repository.get_last_opportunity_ad(Asset.USDT, QuoteSide.BUY) is None


@pytest.mark.asyncio
async def test_opportunity_history_persists_all_ads_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()
    await repository.mark_opportunity_notified(Asset.USDT, QuoteSide.BUY, "offer-a")
    await repository.mark_opportunity_notified(Asset.USDT, QuoteSide.BUY, "offer-b")

    reopened = Repository(database)
    await reopened.initialize()

    assert await reopened.was_opportunity_notified(
        Asset.USDT, QuoteSide.BUY, "offer-a"
    ) is True
    assert await reopened.was_opportunity_notified(
        Asset.USDT, QuoteSide.BUY, "offer-b"
    ) is True


@pytest.mark.asyncio
async def test_configured_owner_conflict_is_not_silent(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.claim_owner(101)

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

    assert await repository.get_owner_id() == 101
    assert await repository.is_paused() is True
    assert [rule.target for rule in await repository.list_alerts()] == ["90"]
    assert (await repository.list_schedules())[0].last_sent_at == (
        "2026-10-10T08:00:00+00:00"
    )
    assert await repository.was_opportunity_notified(
        Asset.USDT, QuoteSide.BUY, "offer-a"
    ) is True


@pytest.mark.asyncio
async def test_schedule_state_persists_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(database)
    await repository.initialize()
    schedule_id = await repository.add_schedule(
        kind=ScheduleKind.DAILY, daily_time="09:30"
    )
    await repository.mark_schedule_sent(schedule_id, "2026-10-10T04:30:00+00:00")

    reopened = Repository(database)
    await reopened.initialize()

    schedule = (await reopened.list_schedules())[0]
    assert schedule.daily_time == "09:30"
    assert schedule.last_sent_at == "2026-10-10T04:30:00+00:00"
    await repository.mark_opportunity_notified(Asset.USDT, QuoteSide.BUY, "offer-1")
    await repository.mark_opportunity_notified(Asset.BTC, QuoteSide.SELL, "offer-2")

    assert await repository.get_last_opportunity_ad(Asset.USDT, QuoteSide.BUY) == "offer-1"
    assert await repository.get_last_opportunity_ad(Asset.BTC, QuoteSide.SELL) == "offer-2"
    assert await repository.get_last_opportunity_ad(Asset.USDT, QuoteSide.SELL) is None

    await repository.configure_opportunities(
        mode=OpportunityMode.BOTH,
        min_spread_percent=Decimal("2"),
    )
    assert await repository.get_last_opportunity_ad(Asset.USDT, QuoteSide.BUY) is None
