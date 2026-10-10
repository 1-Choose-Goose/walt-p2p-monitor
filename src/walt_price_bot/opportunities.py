from decimal import Decimal

from .models import QuoteSide


def opportunity_percent(
    side: QuoteSide,
    *,
    p2p_price: Decimal,
    exchange_price: Decimal,
) -> Decimal:
    if not exchange_price.is_finite() or exchange_price <= 0:
        raise ValueError("exchange price must be positive")
    difference = (
        exchange_price - p2p_price
        if side is QuoteSide.BUY
        else p2p_price - exchange_price
    )
    return (difference / exchange_price * Decimal("100")).quantize(Decimal("0.01"))
