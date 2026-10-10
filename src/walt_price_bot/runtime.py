import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from .alerts import evaluate_percent_change, evaluate_threshold
from .market import MarketUnavailable, select_best_buy_offer, select_best_sell_offer
from .models import AlertKind, AlertRule, Asset, MarketSnapshot, P2PAd, QuoteSide
from .opportunities import mode_includes_side, opportunity_percent
from .repository import Repository
from .scheduling import schedule_is_due
from .ui import _money, _offer_details, format_market_summary
from .walt import WaltClient

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> object: ...


class SnapshotProvider(Protocol):
    async def get_snapshots(self) -> list[MarketSnapshot]: ...


class ExchangeRateProvider(Protocol):
    async def fetch_rub_rate(self, asset: Asset) -> Decimal: ...


class MarketService:
    def __init__(self, client: WaltClient, exchange_client: ExchangeRateProvider) -> None:
        self._client = client
        self._exchange_client = exchange_client

    async def get_snapshots(self) -> list[MarketSnapshot]:
        async def fetch(asset: Asset) -> MarketSnapshot:
            buy_ads, sell_ads, exchange_price = await asyncio.gather(
                self._client.fetch_ads(asset, QuoteSide.BUY),
                self._client.fetch_ads(asset, QuoteSide.SELL),
                self._fetch_exchange_rate(asset),
            )
            try:
                buy = select_best_buy_offer(sell_ads)
            except MarketUnavailable:
                buy = None
            try:
                sell = select_best_sell_offer(buy_ads)
            except MarketUnavailable:
                sell = None
            return MarketSnapshot(
                asset=asset,
                buy=buy,
                sell=sell,
                exchange_price=exchange_price,
            )

        snapshots = await asyncio.gather(*(fetch(asset) for asset in Asset))
        return list(snapshots)

    async def _fetch_exchange_rate(self, asset: Asset) -> Decimal | None:
        try:
            return await self._exchange_client.fetch_rub_rate(asset)
        except Exception as exc:
            logger.warning(
                "Failed to fetch %s exchange rate (%s)", asset.value, type(exc).__name__
            )
            return None


async def run_monitor_cycle(
    repository: Repository,
    provider: SnapshotProvider,
    sender: MessageSender,
    now: datetime,
) -> None:
    if await repository.is_paused():
        return
    owner_id = await repository.get_owner_id()
    if owner_id is None:
        return
    snapshots = await provider.get_snapshots()
    by_asset = {snapshot.asset: snapshot for snapshot in snapshots}

    for rule in await repository.list_alerts(enabled_only=True):
        snapshot = by_asset.get(rule.asset)
        if snapshot is None:
            continue
        offer = snapshot.buy if rule.quote_side is QuoteSide.BUY else snapshot.sell
        if offer is None:
            continue
        price = offer.price
        try:
            if rule.kind is AlertKind.THRESHOLD:
                evaluation = evaluate_threshold(rule, price)
                if evaluation.notify:
                    await sender.send_message(owner_id, _threshold_message(rule, offer))
                    await repository.update_alert_state(rule.id, armed=False)
                elif evaluation.armed != rule.armed:
                    await repository.update_alert_state(rule.id, armed=evaluation.armed)
            else:
                evaluation = evaluate_percent_change(rule, price)
                if evaluation.notify:
                    await sender.send_message(
                        owner_id,
                        _percent_message(
                            rule.asset,
                            rule.quote_side,
                            offer,
                            evaluation.change_percent,
                        ),
                    )
                    await repository.update_alert_state(
                        rule.id, baseline=str(evaluation.next_baseline)
                    )
                elif rule.baseline is None and evaluation.next_baseline is not None:
                    await repository.update_alert_state(
                        rule.id, baseline=str(evaluation.next_baseline)
                    )
        except Exception as exc:
            logger.warning("Failed to process alert %s (%s)", rule.id, type(exc).__name__)

    opportunity_settings = await repository.get_opportunity_settings()
    if opportunity_settings.enabled:
        opportunities: list[tuple[MarketSnapshot, QuoteSide, P2PAd, Decimal]] = []
        for snapshot in snapshots:
            if snapshot.exchange_price is None:
                continue
            for side, offer in (
                (QuoteSide.BUY, snapshot.buy),
                (QuoteSide.SELL, snapshot.sell),
            ):
                if offer is None or not mode_includes_side(opportunity_settings.mode, side):
                    continue
                spread = opportunity_percent(
                    side,
                    p2p_price=offer.price,
                    exchange_price=snapshot.exchange_price,
                )
                if spread < opportunity_settings.min_spread_percent:
                    continue
                if (
                    await repository.get_last_opportunity_ad(snapshot.asset, side)
                    == offer.id
                ):
                    continue
                opportunities.append((snapshot, side, offer, spread))
        if opportunities:
            try:
                await sender.send_message(
                    owner_id,
                    "\n\n".join(
                        _opportunity_message(
                            snapshot,
                            side,
                            offer,
                            spread,
                            opportunity_settings.min_spread_percent,
                        )
                        for snapshot, side, offer, spread in opportunities
                    ),
                )
                for snapshot, side, offer, _ in opportunities:
                    await repository.mark_opportunity_notified(
                        snapshot.asset, side, offer.id
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to deliver opportunity summary (%s)", type(exc).__name__
                )

    for schedule in await repository.list_schedules(enabled_only=True):
        if not schedule_is_due(schedule, now):
            continue
        try:
            await sender.send_message(owner_id, format_market_summary(snapshots))
            await repository.mark_schedule_sent(schedule.id, now.astimezone(UTC).isoformat())
        except Exception as exc:
            logger.warning("Failed to deliver schedule %s (%s)", schedule.id, type(exc).__name__)


async def monitor_forever(
    repository: Repository,
    provider: SnapshotProvider,
    sender: MessageSender,
    *,
    interval_seconds: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        try:
            await run_monitor_cycle(repository, provider, sender, datetime.now(UTC))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Walt monitoring cycle failed")
        await sleep(float(interval_seconds))


def _threshold_message(rule: AlertRule, offer: P2PAd) -> str:
    action = "покупки" if rule.quote_side is QuoteSide.BUY else "продажи"
    direction = "выше или равно" if rule.threshold_direction.value == "above" else "ниже или равно"
    return (
        f"🔔 <b>{rule.asset.value}: достигнут ценовой порог</b>\n"
        f"Цена {action}: {offer.price} ₽\n"
        f"Правило A{rule.id}: {direction} {rule.target} ₽\n"
        f"{_offer_details(offer)}"
    )


def _percent_message(
    asset: Asset,
    side: QuoteSide,
    offer: P2PAd,
    change: Decimal | None,
) -> str:
    action = "покупки" if side is QuoteSide.BUY else "продажи"
    arrow = "↑" if change is not None and change >= 0 else "↓"
    rendered_change = f"{abs(change or Decimal(0)):.2f}"
    return (
        f"📈 <b>{asset.value}: изменение {rendered_change}% {arrow}</b>\n"
        f"Текущая цена {action}: {offer.price} ₽\n"
        f"{_offer_details(offer)}"
    )


def _opportunity_message(
    snapshot: MarketSnapshot,
    side: QuoteSide,
    offer: P2PAd,
    spread: Decimal,
    threshold: Decimal,
) -> str:
    action = "покупка" if side is QuoteSide.BUY else "продажа"
    return (
        f"💎 <b>Расчётно выгодный лот: {action} {snapshot.asset.value}</b>\n"
        f"Биржевой курс Coinbase: {_money(snapshot.exchange_price)} ₽\n"
        f"Цена P2P: {_money(offer.price)} ₽\n"
        f"Расчётная разница: +{str(spread).replace('.', ',')}%\n"
        f"Ваш порог: {str(threshold).replace('.', ',')}%\n"
        f"{_offer_details(offer)}"
    )
