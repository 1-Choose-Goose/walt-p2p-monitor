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


class MultiOfferWaltClient(FakeWaltClient):
    async def fetch_ads(self, asset, side: QuoteSide) -> list[P2PAd]:
        symbol = getattr(asset, "symbol", asset)
        self.calls.append((symbol, side))
        prices = ("91.00", "88.00", "90.00") if side is QuoteSide.SELL else (
            "87.00",
            "92.00",
            "89.00",
        )
        return [
            ad(symbol, side, price, identifier=f"{symbol}-{side}-{index}")
            for index, price in enumerate(prices)
        ]


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
async def test_market_service_reuses_one_fetch_for_best_and_all_candidates() -> None:
    client = MultiOfferWaltClient()

    snapshots = await MarketService(client, FakeExchangeClient()).get_snapshots(
        assets=(SEED_ASSETS[1],)
    )

    assert client.calls == [
        (Asset.USDT, QuoteSide.BUY),
        (Asset.USDT, QuoteSide.SELL),
    ]
    snapshot = snapshots[0]
    assert snapshot.buy.price == Decimal("88.00")
    assert [offer.price for offer in snapshot.buy_offers] == [
        Decimal("88.00"),
        Decimal("90.00"),
        Decimal("91.00"),
    ]
    assert snapshot.sell.price == Decimal("92.00")
    assert [offer.price for offer in snapshot.sell_offers] == [
        Decimal("92.00"),
        Decimal("89.00"),
        Decimal("87.00"),
    ]


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
    assert len(sender.messages) == 1
    assert sender.messages[0][1].count("ниже или равно") == 2
    assert 'href="https://telegram.me/walt/start?startapp=v2-' in sender.messages[0][1]
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
async def test_hidden_exchange_rate_still_drives_opportunity_calculation(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.update_preferences(
        ADMIN_TELEGRAM_ID, show_exchange_rate=False
    )
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    sender = FakeSender()

    await run_monitor_cycle(
        repository,
        FakeProvider(),
        sender,
        datetime.now(UTC),
    )

    assert len(sender.messages) == 1
    assert "выгодный лот" in sender.messages[0][1].lower()


@pytest.mark.asyncio
async def test_interval_summary_respects_hidden_exchange_rate(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.update_preferences(
        ADMIN_TELEGRAM_ID,
        show_exchange_rate=False,
        summary_enabled=True,
        summary_interval_minutes=1,
    )
    sender = FakeSender()

    await run_monitor_cycle(
        repository,
        FakeProvider(),
        sender,
        datetime.now(UTC),
    )

    assert len(sender.messages) == 1
    assert "Coinbase" not in sender.messages[0][1]
    assert "Купить" in sender.messages[0][1]


@pytest.mark.asyncio
async def test_interval_summary_ignores_removed_legacy_category_flag(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.update_preferences(
        ADMIN_TELEGRAM_ID,
        summary_enabled=True,
        summary_interval_minutes=60,
        notify_scheduled=False,
    )
    sender = FakeSender()
    now = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)

    await run_monitor_cycle(repository, FakeProvider(), sender, now)

    assert len(sender.messages) == 1
    assert "актуальные цены" in sender.messages[0][1]
    assert (await repository.get_delivery_times(ADMIN_TELEGRAM_ID))[1] == now.isoformat()


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
async def test_all_profitable_ads_are_recorded_and_digest_shows_first_five(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    offers = tuple(
        ad(Asset.USDT, QuoteSide.SELL, str(price), identifier=f"offer-{index}")
        for index, price in enumerate(("80", "81", "82", "83", "84", "85", "89"))
    )
    provider = FakeProvider()
    provider.snapshots = [
        MarketSnapshot(
            asset=Asset.USDT,
            buy=offers[0],
            sell=None,
            exchange_price=Decimal("90"),
            buy_offers=offers,
        )
    ]
    sender = FakeSender()
    now = datetime.now(UTC)

    await run_monitor_cycle(repository, provider, sender, now)

    assert len(sender.messages) == 1
    assert sender.messages[0][1].count("Расчётно выгодный лот: покупка USDT") == 5
    assert "Ещё предложений в истории: 2" in sender.messages[0][1]
    assert await repository.count_opportunity_history(ADMIN_TELEGRAM_ID, now) == 7


@pytest.mark.asyncio
async def test_large_opportunity_digest_stays_within_telegram_limit(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BOTH,
        min_spread_percent=Decimal("1"),
    )
    now = datetime.now(UTC)
    snapshots = []
    for asset in (Asset.GRAM, Asset.USDT, Asset.BTC, Asset.NOT, Asset.ETH):
        buys = tuple(
            ad(asset, QuoteSide.SELL, str(80 + index), identifier=f"{asset}-buy-{index}")
            for index in range(10)
        )
        sells = tuple(
            ad(asset, QuoteSide.BUY, str(120 - index), identifier=f"{asset}-sell-{index}")
            for index in range(10)
        )
        snapshots.append(
            MarketSnapshot(
                asset=asset,
                buy=buys[0],
                sell=sells[0],
                exchange_price=Decimal("100"),
                captured_at=now,
                buy_offers=buys,
                sell_offers=sells,
            )
        )
    provider = FakeProvider()
    provider.snapshots = snapshots
    sender = FakeSender()

    await run_monitor_cycle(repository, provider, sender, now)

    assert len(sender.messages) == 1
    assert len(sender.messages[0][1]) <= 4000
    assert "Ещё предложений в истории" in sender.messages[0][1]
    assert await repository.count_opportunity_history(ADMIN_TELEGRAM_ID, now) == 100


@pytest.mark.asyncio
async def test_simultaneous_alert_opportunity_and_summary_use_one_delivery(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    alert_id = await repository.add_alert(
        ADMIN_TELEGRAM_ID,
        kind=AlertKind.THRESHOLD,
        asset=Asset.USDT,
        quote_side=QuoteSide.BUY,
        threshold_direction=ThresholdDirection.BELOW,
        target="90",
    )
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    await repository.update_preferences(
        ADMIN_TELEGRAM_ID, summary_enabled=True, summary_interval_minutes=1
    )
    sender = FakeSender()
    now = datetime.now(UTC)

    await run_monitor_cycle(repository, FakeProvider(), sender, now)

    assert len(sender.messages) == 1
    delivered = sender.messages[0][1]
    assert "достигнут ценовой порог" in delivered
    assert "Расчётно выгодный лот" in delivered
    assert "USDT / RUB" in delivered
    rules = await repository.list_alerts(ADMIN_TELEGRAM_ID)
    assert next(rule for rule in rules if rule.id == alert_id).armed is False
    assert (await repository.get_delivery_times(ADMIN_TELEGRAM_ID))[1] == now.isoformat()


@pytest.mark.asyncio
async def test_opportunities_only_include_assets_selected_by_user(tmp_path: Path) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.replace_user_assets(ADMIN_TELEGRAM_ID, (Asset.USDT,))
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    provider = FakeProvider()
    provider.snapshots.append(
        MarketSnapshot(
            asset=Asset.BTC,
            buy=ad(Asset.BTC, QuoteSide.SELL, "7000000"),
            sell=None,
            exchange_price=Decimal("7100000"),
        )
    )
    sender = FakeSender()

    now = datetime.now(UTC)
    await run_monitor_cycle(repository, provider, sender, now)

    assert len(sender.messages) == 1
    assert "USDT" in sender.messages[0][1]
    assert "BTC" not in sender.messages[0][1]
    history = await repository.list_opportunity_history(ADMIN_TELEGRAM_ID, now)
    assert [entry.asset for entry in history] == [Asset.USDT]
    assert "USDT" in history[0].message_text


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
async def test_same_profitable_offer_can_repeat_at_ten_minute_boundary(
    tmp_path: Path,
) -> None:
    repository = await prepared_repository(tmp_path / "bot.db")
    await repository.configure_opportunities(
        ADMIN_TELEGRAM_ID,
        mode=OpportunityMode.BUY,
        min_spread_percent=Decimal("1"),
    )
    provider = FakeProvider()
    sender = FakeSender()
    now = datetime.now(UTC)

    await run_monitor_cycle(repository, provider, sender, now)
    first_id = (await repository.list_opportunity_history(ADMIN_TELEGRAM_ID, now))[0].id
    provider.snapshots = [
        MarketSnapshot(
            asset=snapshot.asset,
            buy=snapshot.buy,
            sell=snapshot.sell,
            exchange_price=snapshot.exchange_price,
            captured_at=now + timedelta(minutes=9, seconds=59),
        )
        for snapshot in provider.snapshots
    ]
    await run_monitor_cycle(repository, provider, sender, now + timedelta(minutes=9, seconds=59))
    provider.snapshots = [
        MarketSnapshot(
            asset=snapshot.asset,
            buy=snapshot.buy,
            sell=snapshot.sell,
            exchange_price=snapshot.exchange_price,
            captured_at=now + timedelta(minutes=10),
        )
        for snapshot in provider.snapshots
    ]
    await run_monitor_cycle(repository, provider, sender, now + timedelta(minutes=10))

    assert len(sender.messages) == 2
    repeated = await repository.list_opportunity_history(
        ADMIN_TELEGRAM_ID, now + timedelta(minutes=10)
    )
    assert len(repeated) == 1
    assert repeated[0].id > first_id


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
