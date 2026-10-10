from collections.abc import Iterable

from .models import P2PAd, QuoteSide


class MarketUnavailable(RuntimeError):
    pass


def select_best_buy_offer(ads: Iterable[P2PAd]) -> P2PAd:
    eligible = [ad for ad in ads if ad.is_online and ad.side is QuoteSide.SELL]
    if not eligible:
        raise MarketUnavailable("no online sell offers")
    return min(eligible, key=lambda ad: ad.price)


def select_best_sell_offer(ads: Iterable[P2PAd]) -> P2PAd:
    eligible = [ad for ad in ads if ad.is_online and ad.side is QuoteSide.BUY]
    if not eligible:
        raise MarketUnavailable("no online buy offers")
    return max(eligible, key=lambda ad: ad.price)
