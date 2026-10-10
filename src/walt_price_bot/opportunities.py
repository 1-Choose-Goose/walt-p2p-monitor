from decimal import Decimal

from .models import OpportunityMode, QuoteSide


def mode_includes_side(mode: OpportunityMode, side: QuoteSide) -> bool:
    return mode is OpportunityMode.BOTH or mode.value == side.value


def opportunity_percent(
    side: QuoteSide,
    *,
    p2p_price: Decimal,
    exchange_price: Decimal,
) -> Decimal:
    if not p2p_price.is_finite() or p2p_price <= 0:
        raise ValueError("P2P price must be positive")
    if not exchange_price.is_finite() or exchange_price <= 0:
        raise ValueError("exchange price must be positive")
    difference = (
        exchange_price - p2p_price
        if side is QuoteSide.BUY
        else p2p_price - exchange_price
    )
    return (difference / exchange_price * Decimal("100")).quantize(Decimal("0.01"))


def opportunity_is_acceptable(
    side: QuoteSide,
    *,
    difference_percent: Decimal,
    min_sell_spread_percent: Decimal,
    max_buy_premium_percent: Decimal,
) -> bool:
    if side is QuoteSide.BUY:
        return difference_percent >= 0 or -difference_percent <= max_buy_premium_percent
    return difference_percent >= min_sell_spread_percent
