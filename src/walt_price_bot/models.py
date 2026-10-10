from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Asset(StrEnum):
    USDT = "USDT"
    TON = "TON"
    BTC = "BTC"


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
