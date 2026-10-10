from decimal import Decimal

import pytest

from walt_price_bot.market import MarketUnavailable, select_best_buy_offer, select_best_sell_offer
from walt_price_bot.models import Asset, P2PAd, QuoteSide


def ad(identifier: str, side: QuoteSide, price: str, *, online: bool = True) -> P2PAd:
    return P2PAd(
        id=identifier,
        nickname="maker",
        asset=Asset.USDT,
        side=side,
        price=Decimal(price),
        min_amount=Decimal("1000"),
        max_amount=Decimal("50000"),
        payments=("sberbank",),
        rating=Decimal("0.99"),
        is_online=online,
    )


def test_buy_offer_is_cheapest_online_sell_ad() -> None:
    offers = [
        ad("expensive", QuoteSide.SELL, "91.00"),
        ad("offline", QuoteSide.SELL, "80.00", online=False),
        ad("best", QuoteSide.SELL, "89.50"),
    ]

    assert select_best_buy_offer(offers).id == "best"


def test_sell_offer_is_highest_online_buy_ad() -> None:
    offers = [ad("low", QuoteSide.BUY, "88.00"), ad("best", QuoteSide.BUY, "90.10")]

    assert select_best_sell_offer(offers).id == "best"


def test_empty_market_is_not_a_zero_price() -> None:
    with pytest.raises(MarketUnavailable):
        select_best_buy_offer([])
