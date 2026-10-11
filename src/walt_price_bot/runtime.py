import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from .alerts import evaluate_percent_change, evaluate_threshold
from .market import select_buy_offers, select_sell_offers
from .models import (
    SEED_ASSETS,
    AlertKind,
    AlertRule,
    Asset,
    AssetCatalogEntry,
    MarketFilters,
    MarketSnapshot,
    P2PAd,
    QuoteSide,
)
from .opportunities import (
    mode_includes_side,
    opportunity_is_acceptable,
    opportunity_percent,
)
from .repository import Repository
from .scheduling import schedule_is_due
from .ui import _money, _offer_details, format_market_summary
from .walt import WaltClient

logger = logging.getLogger(__name__)
MAX_SNAPSHOT_AGE = timedelta(minutes=5)
DEFAULT_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")


class MessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> object: ...


class SnapshotProvider(Protocol):
    async def get_snapshots(
        self,
        assets: Sequence[AssetCatalogEntry] | None = None,
        filters: MarketFilters | None = None,
    ) -> list[MarketSnapshot]: ...


class ExchangeRateProvider(Protocol):
    async def fetch_rub_rate(self, asset: AssetCatalogEntry | Asset) -> Decimal: ...


class MarketService:
    def __init__(self, client: WaltClient, exchange_client: ExchangeRateProvider) -> None:
        self._client = client
        self._exchange_client = exchange_client

    async def get_snapshots(
        self,
        assets: Sequence[AssetCatalogEntry] | None = None,
        filters: MarketFilters | None = None,
    ) -> list[MarketSnapshot]:
        assets = assets or SEED_ASSETS
        filters = filters or MarketFilters()
        async def fetch(entry: AssetCatalogEntry) -> MarketSnapshot:
            buy_request = (
                self._client.fetch_ads(entry, QuoteSide.BUY)
                if QuoteSide.SELL in filters.directions
                else asyncio.sleep(0, result=[])
            )
            sell_request = (
                self._client.fetch_ads(entry, QuoteSide.SELL)
                if QuoteSide.BUY in filters.directions
                else asyncio.sleep(0, result=[])
            )
            exchange_request = (
                self._fetch_exchange_rate(entry)
                if entry.exchange_code is not None
                else asyncio.sleep(0, result=None)
            )
            buy_ads, sell_ads, exchange_price = await asyncio.gather(
                buy_request,
                sell_request,
                exchange_request,
                return_exceptions=True,
            )
            if isinstance(buy_ads, BaseException):
                logger.warning(
                    "Failed to fetch %s buy ads (%s)",
                    entry.symbol.value,
                    type(buy_ads).__name__,
                )
                buy_ads = []
                sell_source_available = False
            else:
                sell_source_available = True
            if isinstance(sell_ads, BaseException):
                logger.warning(
                    "Failed to fetch %s sell ads (%s)",
                    entry.symbol.value,
                    type(sell_ads).__name__,
                )
                sell_ads = []
                buy_source_available = False
            else:
                buy_source_available = True
            if isinstance(exchange_price, BaseException):
                logger.warning(
                    "Failed to fetch %s exchange rate (%s)",
                    entry.symbol.value,
                    type(exchange_price).__name__,
                )
                exchange_price = None
            buy_offers = (
                select_buy_offers(sell_ads, filters)
                if QuoteSide.BUY in filters.directions
                else ()
            )
            sell_offers = (
                select_sell_offers(buy_ads, filters)
                if QuoteSide.SELL in filters.directions
                else ()
            )
            return MarketSnapshot(
                asset=entry.symbol,
                buy=buy_offers[0] if buy_offers else None,
                sell=sell_offers[0] if sell_offers else None,
                exchange_price=exchange_price,
                buy_source_available=buy_source_available,
                sell_source_available=sell_source_available,
                buy_offers=buy_offers,
                sell_offers=sell_offers,
            )

        snapshots = await asyncio.gather(*(fetch(asset) for asset in assets))
        return list(snapshots)

    async def _fetch_exchange_rate(
        self, asset: AssetCatalogEntry | Asset
    ) -> Decimal | None:
        try:
            return await self._exchange_client.fetch_rub_rate(asset)
        except Exception as exc:
            logger.warning(
                "Failed to fetch %s exchange rate (%s)",
                asset.symbol.value if isinstance(asset, AssetCatalogEntry) else asset.value,
                type(exc).__name__,
            )
            return None


async def run_monitor_cycle(
    repository: Repository,
    provider: SnapshotProvider,
    sender: MessageSender,
    now: datetime,
    *,
    telegram_id: int | None = None,
    schedule_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> None:
    resolved_id = telegram_id or await repository.get_owner_id()
    async with repository.opportunity_lock(resolved_id):
        await _run_monitor_cycle_locked(
            repository,
            provider,
            sender,
            now,
            telegram_id=resolved_id,
            schedule_timezone=schedule_timezone,
        )


async def _run_monitor_cycle_locked(
    repository: Repository,
    provider: SnapshotProvider,
    sender: MessageSender,
    now: datetime,
    *,
    telegram_id: int,
    schedule_timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> None:
    if await repository.is_paused(telegram_id):
        return
    preferences = await repository.get_preferences(telegram_id)
    selected = set(preferences.assets)
    assets = tuple(
        entry for entry in await repository.list_assets() if entry.symbol in selected
    )
    filters = MarketFilters(
        directions=preferences.directions,
        min_limit_from=preferences.min_limit_from,
        min_limit_to=preferences.min_limit_to,
        desired_amount=preferences.desired_amount,
        buy_payments=preferences.buy_payments,
        sell_payments=preferences.sell_payments,
    )
    try:
        fetched = await provider.get_snapshots(assets, filters)
    except TypeError:
        fetched = await provider.get_snapshots()
    snapshots = [
        snapshot
        for snapshot in fetched
        if snapshot.asset in selected and _snapshot_is_fresh(snapshot, now)
    ]
    by_asset = {snapshot.asset: snapshot for snapshot in snapshots}
    message_sections: list[str] = []
    pending_alert_disarms: list[int] = []
    pending_alert_baselines: list[tuple[int, str]] = []
    pending_opportunities: list[tuple[Asset, QuoteSide, str, str]] = []
    pending_schedule_ids: list[int] = []
    pending_interval_summary = False

    for rule in await repository.list_alerts(telegram_id, enabled_only=True):
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
                    if preferences.notifications.threshold:
                        message_sections.append(_threshold_message(rule, offer))
                        pending_alert_disarms.append(rule.id)
                elif evaluation.armed != rule.armed:
                    await repository.update_alert_state(
                        telegram_id, rule.id, armed=evaluation.armed
                    )
            else:
                evaluation = evaluate_percent_change(rule, price)
                if evaluation.notify and preferences.notifications.percent:
                    message_sections.append(
                        _percent_message(
                            rule.asset,
                            rule.quote_side,
                            offer,
                            evaluation.change_percent,
                        )
                    )
                    pending_alert_baselines.append(
                        (rule.id, str(evaluation.next_baseline))
                    )
                elif rule.baseline is None and evaluation.next_baseline is not None:
                    await repository.update_alert_state(
                        telegram_id, rule.id, baseline=str(evaluation.next_baseline)
                    )
        except Exception as exc:
            logger.warning("Failed to process alert %s (%s)", rule.id, type(exc).__name__)

    opportunity_settings = await repository.get_opportunity_settings(telegram_id)
    if opportunity_settings.enabled and preferences.notifications.opportunity:
        opportunities: list[tuple[MarketSnapshot, QuoteSide, P2PAd, Decimal]] = []
        recent_keys = await repository.get_recent_opportunity_keys(telegram_id, now)
        for snapshot in snapshots:
            if snapshot.exchange_price is None:
                continue
            for side, offers in (
                (QuoteSide.BUY, snapshot.buy_offers or ((snapshot.buy,) if snapshot.buy else ())),
                (
                    QuoteSide.SELL,
                    snapshot.sell_offers or ((snapshot.sell,) if snapshot.sell else ()),
                ),
            ):
                if not mode_includes_side(opportunity_settings.mode, side):
                    continue
                for offer in offers:
                    spread = opportunity_percent(
                        side,
                        p2p_price=offer.price,
                        exchange_price=snapshot.exchange_price,
                    )
                    if not opportunity_is_acceptable(
                        side,
                        difference_percent=spread,
                        min_sell_spread_percent=opportunity_settings.min_spread_percent,
                        max_buy_premium_percent=(
                            opportunity_settings.max_buy_premium_percent
                        ),
                    ):
                        continue
                    if (snapshot.asset, side, offer.id) in recent_keys:
                        continue
                    opportunities.append((snapshot, side, offer, spread))
        if opportunities:
            opportunity_messages = [
                _opportunity_message(
                    snapshot,
                    side,
                    offer,
                    spread,
                    opportunity_settings.min_spread_percent,
                    opportunity_settings.max_buy_premium_percent,
                )
                for snapshot, side, offer, spread in opportunities
            ]
            message_sections.append(
                _opportunity_digest(opportunities, opportunity_messages)
            )
            pending_opportunities.extend(
                (snapshot.asset, side, offer.id, message_text)
                for (snapshot, side, offer, _), message_text in zip(
                    opportunities, opportunity_messages, strict=True
                )
            )

    scheduled_summary_needed = False
    for schedule in await repository.list_schedules(telegram_id, enabled_only=True):
        if preferences.summary_enabled or not preferences.notifications.scheduled:
            break
        if not schedule_is_due(schedule, now, timezone=schedule_timezone):
            continue
        scheduled_summary_needed = True
        pending_schedule_ids.append(schedule.id)
    if scheduled_summary_needed:
        message_sections.append(
            format_market_summary(
                snapshots,
                tuple(entry.symbol for entry in assets),
                preferences.directions,
                show_exchange_rate=preferences.show_exchange_rate,
            )
        )

    if preferences.summary_enabled and preferences.notifications.scheduled:
        from .monitoring import interval_summary_is_due

        _, last_summary = await repository.get_delivery_times(telegram_id)
        if interval_summary_is_due(
            last_summary, now, preferences.summary_interval_minutes
        ):
            message_sections.append(
                format_market_summary(
                    snapshots,
                    tuple(entry.symbol for entry in assets),
                    preferences.directions,
                    show_exchange_rate=preferences.show_exchange_rate,
                )
            )
            pending_interval_summary = True

    if not message_sections:
        return
    try:
        await sender.send_message(telegram_id, _delivery_text(message_sections))
    except Exception as exc:
        logger.warning("Failed to deliver monitoring events (%s)", type(exc).__name__)
        return

    for alert_id in pending_alert_disarms:
        await repository.update_alert_state(telegram_id, alert_id, armed=False)
    for alert_id, baseline in pending_alert_baselines:
        await repository.update_alert_state(telegram_id, alert_id, baseline=baseline)
    if pending_opportunities:
        await repository.mark_opportunities_notified(
            telegram_id, pending_opportunities, now
        )
    sent_at = now.astimezone(UTC).isoformat()
    for schedule_id in pending_schedule_ids:
        await repository.mark_schedule_sent(telegram_id, schedule_id, sent_at)
    if pending_interval_summary:
        await repository.mark_summary_sent(telegram_id, sent_at)


async def monitor_forever(
    repository: Repository,
    provider: SnapshotProvider,
    sender: MessageSender,
    *,
    interval_seconds: int,
    schedule_timezone: ZoneInfo = DEFAULT_TIMEZONE,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    last_history_cleanup: datetime | None = None
    while True:
        try:
            now = datetime.now(UTC)
            if (
                last_history_cleanup is None
                or now >= last_history_cleanup + timedelta(minutes=1)
            ):
                try:
                    await repository.prune_opportunity_history(now)
                except Exception as exc:
                    logger.warning(
                        "Opportunity history cleanup failed (%s)",
                        type(exc).__name__,
                    )
                last_history_cleanup = now
            await run_monitor_cycle(
                repository,
                provider,
                sender,
                now,
                schedule_timezone=schedule_timezone,
            )
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


def _opportunity_digest(
    opportunities: list[tuple[MarketSnapshot, QuoteSide, P2PAd, Decimal]],
    messages: list[str],
) -> str:
    grouped: dict[tuple[Asset, QuoteSide], list[str]] = {}
    for (snapshot, side, _, _), message in zip(
        opportunities, messages, strict=True
    ):
        grouped.setdefault((snapshot.asset, side), []).append(message)
    sections: list[str] = []
    visible_count = 0
    for group_messages in grouped.values():
        for message in group_messages[:5]:
            candidate = "\n\n".join([*sections, message])
            if len(candidate) > 2400:
                break
            sections.append(message)
            visible_count += 1
    hidden = len(messages) - visible_count
    if hidden > 0:
        sections.append(f"Ещё предложений в истории: {hidden}")
    return "\n\n".join(sections)


def _delivery_text(sections: list[str]) -> str:
    full = "\n\n".join(sections)
    if len(full) <= 4000:
        return full
    headlines = [re.sub(r"<[^>]+>", "", section.splitlines()[0]) for section in sections]
    compact = (
        "🔔 <b>Новые события мониторинга</b>\n"
        + "\n".join(f"• {headline}" for headline in headlines)
        + "\n\nПодробности выгодных лотов доступны в истории в течение 10 минут."
    )
    return compact[:4000]


def _opportunity_message(
    snapshot: MarketSnapshot,
    side: QuoteSide,
    offer: P2PAd,
    spread: Decimal,
    threshold: Decimal,
    max_buy_premium: Decimal,
) -> str:
    action = "покупка" if side is QuoteSide.BUY else "продажа"
    if side is QuoteSide.BUY and spread < 0:
        comparison = (
            f"Переплата к бирже: {str(-spread).replace('.', ',')}% "
            f"(допуск до {str(max_buy_premium).replace('.', ',')}%)"
        )
    elif side is QuoteSide.BUY:
        comparison = f"Цена ниже биржи на {str(spread).replace('.', ',')}%"
    else:
        comparison = f"Цена выше биржи на {str(spread).replace('.', ',')}%"
    return (
        f"💎 <b>Расчётно выгодный лот: {action} {snapshot.asset.value}</b>\n"
        f"Биржевой курс Coinbase: {_money(snapshot.exchange_price)} ₽\n"
        f"Цена P2P: {_money(offer.price)} ₽\n"
        f"{comparison}\n"
        + (
            f"Минимум для продажи: {str(threshold).replace('.', ',')}%\n"
            if side is QuoteSide.SELL
            else ""
        )
        + _offer_details(offer)
    )


def _snapshot_is_fresh(snapshot: MarketSnapshot, now: datetime) -> bool:
    if snapshot.captured_at.tzinfo is None or now.tzinfo is None:
        return False
    age = now - snapshot.captured_at
    return -timedelta(seconds=30) <= age <= MAX_SNAPSHOT_AGE
