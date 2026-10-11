import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .credentials import CredentialCipher
from .exchange import CoinbaseRatesClient
from .models import AccessStatus, MarketFilters, QuietHours, User, UserPreferences
from .repository import Repository
from .runtime import MarketService, MessageSender, run_monitor_cycle
from .walt import WaltClient

logger = logging.getLogger(__name__)


def interval_summary_is_due(
    last_sent_at: str | None, now: datetime, interval_minutes: int
) -> bool:
    if interval_minutes < 1 or interval_minutes > 1440:
        raise ValueError("summary interval must be between 1 and 1440 minutes")
    if last_sent_at is None:
        return True
    last_sent = datetime.fromisoformat(last_sent_at)
    if last_sent.tzinfo is None or now.tzinfo is None:
        raise ValueError("summary timestamps must be timezone-aware")
    return now.astimezone(UTC) >= last_sent.astimezone(UTC) + timedelta(
        minutes=interval_minutes
    )


def in_quiet_hours(now: datetime, quiet: QuietHours) -> bool:
    if not quiet.enabled:
        return False
    current = now.time().replace(tzinfo=None)
    start = time.fromisoformat(quiet.start)
    end = time.fromisoformat(quiet.end)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def market_filters(preferences: UserPreferences) -> MarketFilters:
    return MarketFilters(
        directions=preferences.directions,
        min_limit_from=preferences.min_limit_from,
        min_limit_to=preferences.min_limit_to,
        desired_amount=preferences.desired_amount,
        buy_payments=preferences.buy_payments,
        sell_payments=preferences.sell_payments,
    )


class UserMarketProviderFactory:
    def __init__(
        self,
        session: Any,
        repository: Repository,
        cipher: CredentialCipher,
        exchange_client: Any | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._cipher = cipher
        self._exchange = exchange_client or CoinbaseRatesClient(session=session)

    async def for_user(self, user: User) -> MarketService:
        ciphertext = await self._repository.get_encrypted_credential(user.telegram_id)
        if ciphertext is None:
            raise ValueError("Walt credential is missing")
        token = self._cipher.decrypt(ciphertext)
        return MarketService(
            WaltClient(session=self._session, api_key=token.get_secret_value()),
            self._exchange,
        )


async def run_user_cycle(
    user: User,
    repository: Repository,
    provider: MarketService,
    sender: MessageSender,
    now: datetime,
) -> None:
    current = await repository.get_user(user.telegram_id)
    if current is None or current.status is not AccessStatus.ACTIVE:
        return
    preferences = await repository.get_preferences(user.telegram_id)
    timezone = ZoneInfo(preferences.timezone)
    local_now = now.astimezone(timezone)
    if in_quiet_hours(local_now, preferences.quiet_hours):
        return
    last_monitor, _ = await repository.get_delivery_times(user.telegram_id)
    if last_monitor is not None:
        previous = datetime.fromisoformat(last_monitor)
        if now.astimezone(UTC) < previous.astimezone(UTC) + timedelta(
            seconds=preferences.monitor_interval_seconds
        ):
            return
    await repository.mark_monitor_cycle(user.telegram_id, now.astimezone(UTC).isoformat())
    await run_monitor_cycle(
        repository,
        provider,
        sender,
        now,
        telegram_id=user.telegram_id,
        schedule_timezone=timezone,
    )


async def run_multi_user_cycle(
    repository: Repository,
    factory: UserMarketProviderFactory,
    sender: MessageSender,
    now: datetime,
    *,
    concurrency: int = 5,
    run_user: Callable[..., Awaitable[None]] = run_user_cycle,
) -> None:
    users: list[User] = []
    offset = 0
    while True:
        page = await repository.list_users(
            status=AccessStatus.ACTIVE, limit=100, offset=offset
        )
        users.extend(page)
        if len(page) < 100:
            break
        offset += len(page)
    semaphore = asyncio.Semaphore(concurrency)

    async def isolated(user: User) -> None:
        try:
            provider = await factory.for_user(user)
            async with semaphore:
                await run_user(user, repository, provider, sender, now)
        except Exception as exc:
            logger.warning(
                "Monitoring failed for Telegram user %s (%s)",
                user.telegram_id,
                type(exc).__name__,
            )

    await asyncio.gather(*(isolated(user) for user in users))


async def monitor_multi_user_forever(
    repository: Repository,
    factory: UserMarketProviderFactory,
    sender: MessageSender,
    *,
    tick_seconds: int = 15,
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
            await run_multi_user_cycle(
                repository, factory, sender, now
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Multi-user monitoring cycle failed")
        await sleep(float(tick_seconds))
