import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from walt_price_bot.models import (
    SEED_ASSETS,
    AlertKind,
    Asset,
    MarketSnapshot,
    OpportunityMode,
    P2PAd,
    QuoteSide,
    ScheduleKind,
    ThresholdDirection,
)
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, Repository
from walt_price_bot.runtime import MarketService, run_monitor_cycle


def ad(
    asset: Asset, side: QuoteSide, price: str, *, identifier: str | None = None
) -> P2PAd:
    return P2PAd(
        id=identifier or f"{asset}-{side}",
        user_id="67890",
        nickname="maker",
        asset=asset,
        side=side,
        price=Decimal(price),
        min_amount=Decimal("1000"),
        max_amount=Decimal("50000"),
        payments=("sbp",),
        rating=Decimal("0.99"),
        is_online=True,
    )


class FakeWaltClient:
    def __init__(self) -> None:
        self.calls: list[tuple[Asset, QuoteSide]] = []

    async def fetch_ads(self, asset, side: QuoteSide) -> list[P2PAd]:
        symbol = getattr(asset, "symbol", asset)
        self.calls.append((symbol, side))
        price = "90.00" if side is QuoteSide.SELL else "89.00"
        return [ad(symbol, side, price)]


class FakeExchangeClient:
    def __init__(self) -> None:
        self.calls: list[Asset] = []

    async def fetch_rub_rate(self, asset) -> Decimal:
        symbol = getattr(asset, "symbol", asset)
        self.calls.append(symbol)
        return {
            Asset.USDT: Decimal("85.05"),
            Asset.GRAM: Decimal("124.08"),
            Asset.BTC: Decimal("7047246.71"),
            Asset.NOT: Decimal("0.15"),
            Asset.ETH: Decimal("350000"),
        }[symbol]


class FailingExchangeClient:
    async def fetch_rub_rate(self, asset: Asset) -> Decimal:
        raise RuntimeError(f"{asset.value} unavailable")


class EmptyWaltClient:
    async def fetch_ads(self, asset: Asset, side: QuoteSide) -> list[P2PAd]:
        return []


class PartiallyFailingWaltClient(FakeWaltClient):
    async def fetch_ads(self, asset, side: QuoteSide) -> list[P2PAd]:
        if getattr(asset, "symbol", asset) == Asset.BTC:
            raise RuntimeError("BTC unavailable")
        return await super().fetch_ads(asset, side)


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.snapshots = [
            MarketSnapshot(
                asset=Asset.USDT,
                buy=ad(Asset.USDT, QuoteSide.SELL, "89.00"),
                sell=ad(Asset.USDT, QuoteSide.BUY, "91.00"),
                exchange_price=Decimal("90.00"),
            ),
            MarketSnapshot(
                asset=Asset.TON,
                buy=ad(Asset.TON, QuoteSide.SELL, "125.00"),
                sell=ad(Asset.TON, QuoteSide.BUY, "124.00"),
                exchange_price=Decimal("124.00"),
            ),
        ]

    async def get_snapshots(self) -> list[MarketSnapshot]:
        self.calls += 1
        return self.snapshots


class FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_market_service_fetches_each_market_once() -> None:
    client = FakeWaltClient()
    exchange_client = FakeExchangeClient()

    snapshots = await MarketService(client, exchange_client).get_snapshots()

    assert len(snapshots) == 5
    assert set(client.calls) == {
        (Asset.USDT, QuoteSide.BUY),
        (Asset.USDT, QuoteSide.SELL),
        (Asset.GRAM, QuoteSide.BUY),
        (Asset.GRAM, QuoteSide.SELL),
        (Asset.BTC, QuoteSide.BUY),
        (Asset.BTC, QuoteSide.SELL),
        (Asset.NOT, QuoteSide.BUY),
        (Asset.NOT, QuoteSide.SELL),
        (Asset.ETH, QuoteSide.BUY),
        (Asset.ETH, QuoteSide.SELL),
    }
    assert exchange_client.calls == [entry.symbol for entry in SEED_ASSETS]
    assert {snapshot.asset: snapshot.exchange_price for snapshot in snapshots} == {
        Asset.USDT: Decimal("85.05"),
        Asset.GRAM: Decimal("124.08"),
        Asset.BTC: Decimal("7047246.71"),
        Asset.NOT: Decimal("0.15"),
        Asset.ETH: Decimal("350000"),
    }


@pytest.mark.asyncio
async def test_market_service_keeps_exchange_rates_when_p2p_offers_are_unavailable() -> None:
    snapshots = await MarketService(EmptyWaltClient(), FakeExchangeClient()).get_snapshots()

    assert len(snapshots) == 5
    assert all(snapshot.buy is None and snapshot.sell is None for snapshot in snapshots)
    assert all(snapshot.exchange_price is not None for snapshot in snapshots)


@pytest.mark.asyncio
async def test_market_service_isolates_one_asset_failure() -> None:
    snapshots = await MarketService(
        PartiallyFailingWaltClient(), FakeExchangeClient()
    ).get_snapshots()

    by_asset = {snapshot.asset: snapshot for snapshot in snapshots}
    assert by_asset[Asset.USDT].buy is not None
    assert by_asset[Asset.GRAM].sell is not None
    assert by_asset[Asset.BTC].buy is None
    assert by_asset[Asset.BTC].sell is None
    assert by_asset[Asset.BTC].exchange_price is not None
    assert by_asset[Asset.BTC].buy_source_available is False
    assert by_asset[Asset.BTC].sell_source_available is False


@pytest.mark.asyncio
async def test_coinbase_failure_keeps_walt_without_opportunity_signal(
    tmp_path: Path,
) -> None:
    provider = MarketService(FakeWaltClient(), FailingExchangeClient())
    snapshots = await provider.get_snapshots()
    assert all(snapshot.buy is not None for snapshot in snapshots)
    assert all(snapshot.exchange_price is None for snapshot in snapshots)

    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BOTH,
        min_spread_percent=Decimal("0.5"),
    )
    sender = FakeSender()
    await run_monitor_cycle(repository, provider, sender, datetime.now(UTC))
    assert sender.messages == []


async def prepared_repository(path: Path) -> Repository:
    repository = Repository(path)
    await repository.initialize()
    return repository


@pytest.mark.asyncio
async def test_monitor_uses_one_snapshot_for_multiple_rules_and_commits_success(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    for target in ("90.00", "90.50"):
        await repository.add_alert(
            ADMIN_TELEGRAM_ID,
            kind=AlertKind.THRESHOLD,
            asset=Asset.USDT,
            quote_side=QuoteSide.BUY,
            threshold_direction=ThresholdDirection.BELOW,
            target=target,
        )
    provider = FakeProvider()
    sender = FakeSender()

    await run_monitor_cycle(repository, provider, sender, datetime.now(UTC))

    assert provider.calls == 1
    assert len(sender.messages) == 2
    assert all("ниже или равно" in text for _, text in sender.messages)
    assert all(
        'href="https://telegram.me/walt/start?startapp=v2-' in text
        for _, text in sender.messages
    )
    assert all(
        rule.armed is False
        for rule in await repository.list_alerts(ADMIN_TELEGRAM_ID)
    )


@pytest.mark.asyncio
async def test_failed_send_does_not_consume_alert(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.add_alert(
        ADMIN_TELEGRAM_ID,
        kind=AlertKind.PERCENT,
        asset=Asset.USDT,
        quote_side=QuoteSide.SELL,
        target="1",
        baseline="89.00",
    )

    await run_monitor_cycle(repository, FakeProvider(), FakeSender(fail=True), datetime.now(UTC))

    assert (await repository.list_alerts(ADMIN_TELEGRAM_ID))[0].baseline == "89.00"


@pytest.mark.asyncio
async def test_paused_monitor_does_not_fetch_market(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.set_paused(ADMIN_TELEGRAM_ID, True)
    provider = FakeProvider()

    await run_monitor_cycle(repository, provider, FakeSender(), datetime.now(UTC))

    assert provider.calls == 0


@pytest.mark.asyncio
async def test_schedule_is_marked_only_after_successful_delivery(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    schedule_id = await repository.add_schedule(
        ADMIN_TELEGRAM_ID, kind=ScheduleKind.INTERVAL, interval_hours=1
    )
    now = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)

    await run_monitor_cycle(repository, FakeProvider(), FakeSender(fail=True), now)
    assert (await repository.list_schedules(ADMIN_TELEGRAM_ID))[0].last_sent_at is None

    await run_monitor_cycle(repository, FakeProvider(), FakeSender(), now)
    schedule = (await repository.list_schedules(ADMIN_TELEGRAM_ID))[0]
    assert schedule.id == schedule_id
    assert schedule.last_sent_at == now.isoformat()


@pytest.mark.asyncio
async def test_profitable_offer_notifies_selected_side_once(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    sender = FakeSender()
    now = datetime.now(UTC)

    await run_monitor_cycle(repository, FakeProvider(), sender, now)
    await run_monitor_cycle(repository, FakeProvider(), sender, now)

    assert len(sender.messages) == 1
    text = sender.messages[0][1]
    assert "Расчётно выгодный лот: покупка USDT" in text
    assert "Биржевой курс Coinbase: 90 ₽" in text
    assert "Цена P2P: 89 ₽" in text
    assert "Цена ниже биржи на 1,11%" in text
    assert "Открыть объявление" in text


@pytest.mark.asyncio
async def test_buy_slightly_above_exchange_notifies_within_premium(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("2"),
        max_buy_premium_percent=Decimal("2"),
    )
    provider = FakeProvider()
    provider.snapshots = [
        MarketSnapshot(
            asset=Asset.USDT,
            buy=ad(Asset.USDT, QuoteSide.SELL, "86"),
            sell=None,
            exchange_price=Decimal("85"),
        )
    ]
    sender = FakeSender()

    await run_monitor_cycle(repository, provider, sender, datetime.now(UTC))

    assert len(sender.messages) == 1
    assert "Переплата к бирже: 1,18% (допуск до 2%)" in sender.messages[0][1]


@pytest.mark.asyncio
async def test_opportunity_mode_filters_side_and_threshold(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.SELL,
        min_spread_percent=Decimal("1"),
    )
    sender = FakeSender()

    await run_monitor_cycle(repository, FakeProvider(), sender, datetime.now(UTC))

    assert len(sender.messages) == 1
    assert "Расчётно выгодный лот: продажа USDT" in sender.messages[0][1]


@pytest.mark.asyncio
async def test_failed_opportunity_send_is_retried(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    now = datetime.now(UTC)

    await run_monitor_cycle(repository, FakeProvider(), FakeSender(fail=True), now)
    sender = FakeSender()
    await run_monitor_cycle(repository, FakeProvider(), sender, now)

    assert len(sender.messages) == 1


@pytest.mark.asyncio
async def test_multiple_opportunities_are_delivered_in_one_message(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BOTH,
        min_spread_percent=Decimal("1"),
    )
    sender = FakeSender()

    await run_monitor_cycle(repository, FakeProvider(), sender, datetime.now(UTC))

    assert len(sender.messages) == 1
    assert "покупка USDT" in sender.messages[0][1]
    assert "продажа USDT" in sender.messages[0][1]


@pytest.mark.asyncio
async def test_opportunity_history_prevents_a_b_a_repeat(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    provider = FakeProvider()
    sender = FakeSender()
    now = datetime.now(UTC)

    provider.snapshots[0] = MarketSnapshot(
        asset=Asset.USDT,
        buy=ad(Asset.USDT, QuoteSide.SELL, "89.00", identifier="offer-a"),
        sell=None,
        exchange_price=Decimal("90.00"),
    )
    await run_monitor_cycle(repository, provider, sender, now)

    provider.snapshots[0] = MarketSnapshot(
        asset=Asset.USDT,
        buy=ad(Asset.USDT, QuoteSide.SELL, "88.00", identifier="offer-b"),
        sell=None,
        exchange_price=Decimal("90.00"),
    )
    await run_monitor_cycle(repository, provider, sender, now)

    provider.snapshots[0] = MarketSnapshot(
        asset=Asset.USDT,
        buy=ad(Asset.USDT, QuoteSide.SELL, "89.00", identifier="offer-a"),
        sell=None,
        exchange_price=Decimal("90.00"),
    )
    await run_monitor_cycle(repository, provider, sender, now)

    assert len(sender.messages) == 2


@pytest.mark.asyncio
async def test_concurrent_cycles_send_opportunity_once(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    sender = FakeSender()
    now = datetime.now(UTC)

    await asyncio.gather(
        run_monitor_cycle(repository, FakeProvider(), sender, now),
        run_monitor_cycle(repository, FakeProvider(), sender, now),
    )

    assert len(sender.messages) == 1


@pytest.mark.asyncio
async def test_stale_snapshot_does_not_create_signal(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    provider = FakeProvider()
    now = datetime.now(UTC)
    current = provider.snapshots[0]
    provider.snapshots[0] = MarketSnapshot(
        asset=current.asset,
        buy=current.buy,
        sell=current.sell,
        exchange_price=current.exchange_price,
        captured_at=now - timedelta(minutes=6),
    )
    sender = FakeSender()

    await run_monitor_cycle(repository, provider, sender, now)

    assert sender.messages == []
