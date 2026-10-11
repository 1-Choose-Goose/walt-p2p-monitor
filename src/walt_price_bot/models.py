import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

_TICKER_PATTERN = re.compile(r"[A-Z0-9]{1,16}\Z")


@dataclass(frozen=True, slots=True)
class Asset:
    symbol: str

    GRAM: ClassVar["Asset"]
    TON: ClassVar["Asset"]
    USDT: ClassVar["Asset"]
    BTC: ClassVar["Asset"]
    NOT: ClassVar["Asset"]
    ETH: ClassVar["Asset"]

    def __post_init__(self) -> None:
        normalized = self.symbol.strip().upper()
        if not _TICKER_PATTERN.fullmatch(normalized):
            raise ValueError("invalid asset ticker")
        object.__setattr__(self, "symbol", normalized)

    @property
    def value(self) -> str:
        return self.symbol

    def __str__(self) -> str:
        return self.symbol


GRAM = Asset("GRAM")
TON = Asset("TON")
USDT = Asset("USDT")
BTC = Asset("BTC")
NOT = Asset("NOT")
ETH = Asset("ETH")

Asset.GRAM = GRAM
Asset.TON = TON
Asset.USDT = USDT
Asset.BTC = BTC
Asset.NOT = NOT
Asset.ETH = ETH


@dataclass(frozen=True, slots=True)
class AssetCatalogEntry:
    symbol: Asset
    walt_code: str
    exchange_code: str | None
    active: bool = True
    sort_order: int = 0

    def __post_init__(self) -> None:
        walt_code = self.walt_code.strip().upper()
        exchange_code = self.exchange_code.strip().upper() if self.exchange_code else None
        if not _TICKER_PATTERN.fullmatch(walt_code):
            raise ValueError("invalid Walt asset ticker")
        if exchange_code is not None and not _TICKER_PATTERN.fullmatch(exchange_code):
            raise ValueError("invalid exchange asset ticker")
        if self.sort_order < 0:
            raise ValueError("sort order cannot be negative")
        object.__setattr__(self, "walt_code", walt_code)
        object.__setattr__(self, "exchange_code", exchange_code)


SEED_ASSETS = (
    AssetCatalogEntry(GRAM, "TON", "TON", sort_order=0),
    AssetCatalogEntry(USDT, "USDT", "USDT", sort_order=1),
    AssetCatalogEntry(BTC, "BTC", "BTC", sort_order=2),
    AssetCatalogEntry(NOT, "NOT", "NOT", sort_order=3),
    AssetCatalogEntry(ETH, "ETH", "ETH", sort_order=4),
)


class QuoteSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OpportunityMode(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    BOTH = "BOTH"


class AlertKind(StrEnum):
    THRESHOLD = "threshold"
    PERCENT = "percent"


class ThresholdDirection(StrEnum):
    ABOVE = "above"
    BELOW = "below"


class ScheduleKind(StrEnum):
    INTERVAL = "interval"
    DAILY = "daily"


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"
    PREMIUM = "PREMIUM"


class AccessStatus(StrEnum):
    PENDING = "PENDING"
    AWAITING_TOKEN = "AWAITING_TOKEN"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class User:
    telegram_id: int
    phone: str | None
    role: UserRole
    status: AccessStatus
    first_name: str | None = None
    username: str | None = None
    version: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    threshold: bool = True
    percent: bool = True
    scheduled: bool = True
    opportunity: bool = True


@dataclass(frozen=True, slots=True)
class QuietHours:
    enabled: bool = False
    start: str = "22:00"
    end: str = "08:00"


@dataclass(frozen=True, slots=True)
class UserPreferences:
    assets: tuple[Asset, ...] = field(
        default_factory=lambda: tuple(entry.symbol for entry in SEED_ASSETS)
    )
    directions: tuple[QuoteSide, ...] = (QuoteSide.BUY, QuoteSide.SELL)
    min_limit_from: Decimal = Decimal("500")
    min_limit_to: Decimal | None = Decimal("2000")
    desired_amount: Decimal | None = None
    buy_payments: tuple[str, ...] = ("sbp", "sberbankru", "russian_banks")
    sell_payments: tuple[str, ...] = ("any",)
    monitor_interval_seconds: int = 30
    summary_interval_minutes: int = 60
    summary_enabled: bool = False
    timezone: str = "Asia/Yekaterinburg"
    show_exchange_rate: bool = True
    notifications: NotificationPreferences = field(default_factory=NotificationPreferences)
    quiet_hours: QuietHours = field(default_factory=QuietHours)


@dataclass(frozen=True, slots=True)
class MarketFilters:
    directions: tuple[QuoteSide, ...] = (QuoteSide.BUY, QuoteSide.SELL)
    min_limit_from: Decimal = Decimal("500")
    min_limit_to: Decimal | None = Decimal("2000")
    desired_amount: Decimal | None = None
    buy_payments: tuple[str, ...] = ("sbp", "sberbankru", "russian_banks")
    sell_payments: tuple[str, ...] = ("any",)


@dataclass(frozen=True, slots=True)
class P2PAd:
    id: str
    user_id: str
    nickname: str
    asset: Asset
    side: QuoteSide
    price: Decimal
    min_amount: Decimal
    max_amount: Decimal
    payments: tuple[str, ...]
    rating: Decimal | None
    is_online: bool
    number: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    asset: Asset
    buy: P2PAd | None
    sell: P2PAd | None
    exchange_price: Decimal | None = None
    buy_source_available: bool = True
    sell_source_available: bool = True
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AlertRule:
    id: int
    kind: AlertKind
    asset: Asset
    quote_side: QuoteSide
    threshold_direction: ThresholdDirection | None
    target: str
    baseline: str | None
    armed: bool
    enabled: bool


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    id: int
    kind: ScheduleKind
    interval_hours: int | None
    daily_time: str | None
    last_sent_at: str | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class OpportunitySettings:
    enabled: bool
    mode: OpportunityMode
    min_spread_percent: Decimal
    max_buy_premium_percent: Decimal = Decimal("2")


@dataclass(frozen=True, slots=True)
class OpportunityHistoryEntry:
    id: int
    asset: Asset
    quote_side: QuoteSide
    ad_id: str
    message_text: str
    notified_at: datetime
    expires_at: datetime
