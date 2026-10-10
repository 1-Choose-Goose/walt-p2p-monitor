from collections.abc import Iterable

from .models import P2PAd, QuoteSide

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


def select_best_buy_offer(ads: Iterable[P2PAd]) -> P2PAd:
    eligible = [
        ad
        for ad in ads
        if ad.is_online
        and ad.side is QuoteSide.SELL
        and _has_valid_values(ad)
        and MIN_PURCHASE_LIMIT <= ad.min_amount <= MAX_PURCHASE_LIMIT
        and _supports_allowed_purchase_payment(ad)
    ]
    if not eligible:
        raise MarketUnavailable("no online sell offers")
    return min(eligible, key=lambda ad: ad.price)


def _supports_allowed_purchase_payment(ad: P2PAd) -> bool:
    payments = {payment.lower() for payment in ad.payments}
    return bool(payments & ALLOWED_PURCHASE_PAYMENTS)


def _has_valid_values(ad: P2PAd) -> bool:
    return (
        ad.price.is_finite()
        and ad.price > 0
        and ad.min_amount.is_finite()
        and ad.min_amount >= 0
        and ad.max_amount.is_finite()
        and ad.max_amount >= ad.min_amount
    )


def select_best_sell_offer(ads: Iterable[P2PAd]) -> P2PAd:
    eligible = [
        ad
        for ad in ads
        if ad.is_online
        and ad.side is QuoteSide.BUY
        and _has_valid_values(ad)
        and MIN_PURCHASE_LIMIT <= ad.min_amount <= MAX_PURCHASE_LIMIT
    ]
    if not eligible:
        raise MarketUnavailable("no online buy offers")
    return max(eligible, key=lambda ad: ad.price)
