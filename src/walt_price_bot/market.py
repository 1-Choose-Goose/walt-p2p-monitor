from collections.abc import Iterable

from .models import MarketFilters, P2PAd, QuoteSide

MIN_PURCHASE_LIMIT = 500
MAX_PURCHASE_LIMIT = 2000
ALLOWED_PURCHASE_PAYMENTS = {
    "akbarsbank",
    "alfabank",
    "alfabankru",
    "gazprombank",
    "mtsbank",
    "ozonbank",
    "raiffeisenbankru",
    "rosbank",
    "rosselkhozbank",
    "russianstandardbank",
    "sbp",
    "sberbank",
    "sberbankru",
    "tinkoff",
    "vtbbankru",
}


class MarketUnavailable(RuntimeError):
    pass


def select_best_buy_offer(
    ads: Iterable[P2PAd], filters: MarketFilters | None = None
) -> P2PAd:
    filters = filters or MarketFilters()
    eligible = [
        ad
        for ad in ads
        if ad.is_online
        and ad.side is QuoteSide.SELL
        and _has_valid_values(ad)
        and _matches_limits(ad, filters)
        and _supports_payments(ad, filters.buy_payments)
    ]
    if not eligible:
        raise MarketUnavailable("no online sell offers")
    return min(eligible, key=lambda ad: ad.price)


def _supports_allowed_purchase_payment(ad: P2PAd) -> bool:
    payments = {payment.lower() for payment in ad.payments}
    return bool(payments & ALLOWED_PURCHASE_PAYMENTS)


def _supports_payments(ad: P2PAd, configured: tuple[str, ...]) -> bool:
    if "any" in configured:
        return True
    payments = {payment.lower() for payment in ad.payments}
    allowed = {payment.lower() for payment in configured}
    if "sberbankru" in allowed:
        allowed.add("sberbank")
    if "alfabankru" in allowed:
        allowed.add("alfabank")
    if "russian_banks" in allowed:
        allowed |= ALLOWED_PURCHASE_PAYMENTS
    return bool(payments & allowed)


def _matches_limits(ad: P2PAd, filters: MarketFilters) -> bool:
    if ad.min_amount < filters.min_limit_from:
        return False
    if filters.min_limit_to is not None and ad.min_amount > filters.min_limit_to:
        return False
    return filters.desired_amount is None or (
        ad.min_amount <= filters.desired_amount <= ad.max_amount
    )


def _has_valid_values(ad: P2PAd) -> bool:
    return (
        ad.price.is_finite()
        and ad.price > 0
        and ad.min_amount.is_finite()
        and ad.min_amount >= 0
        and ad.max_amount.is_finite()
        and ad.max_amount >= ad.min_amount
    )


def select_best_sell_offer(
    ads: Iterable[P2PAd], filters: MarketFilters | None = None
) -> P2PAd:
    filters = filters or MarketFilters()
    eligible = [
        ad
        for ad in ads
        if ad.is_online
        and ad.side is QuoteSide.BUY
        and _has_valid_values(ad)
        and _matches_limits(ad, filters)
        and _supports_payments(ad, filters.sell_payments)
    ]
    if not eligible:
        raise MarketUnavailable("no online buy offers")
    return max(eligible, key=lambda ad: ad.price)
