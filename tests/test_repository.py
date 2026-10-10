import asyncio
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
