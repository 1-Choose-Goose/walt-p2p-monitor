import asyncio
import sqlite3
from pathlib import Path

import pytest

from walt_price_bot.models import AlertKind, Asset, QuoteSide, ThresholdDirection
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
