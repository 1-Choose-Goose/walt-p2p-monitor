from decimal import Decimal

import pytest

from walt_price_bot.market import MarketUnavailable, select_best_buy_offer, select_best_sell_offer
from walt_price_bot.models import Asset, P2PAd, QuoteSide


def ad(
    identifier: str,
    side: QuoteSide,
    price: str,
    *,
    online: bool = True,
    min_amount: str = "1000",
    payments: tuple[str, ...] = ("sbp",),
) -> P2PAd:
    return P2PAd(
        id=identifier,
        nickname="maker",
        asset=Asset.USDT,
        side=side,
        price=Decimal(price),
        min_amount=Decimal(min_amount),
        max_amount=Decimal("50000"),
        payments=payments,
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


def test_buy_offer_requires_minimum_limit_between_500_and_2000_rubles() -> None:
    offers = [
        ad("too-low", QuoteSide.SELL, "80.00", min_amount="499"),
        ad("too-high", QuoteSide.SELL, "81.00", min_amount="2000.01"),
        ad("lower-bound", QuoteSide.SELL, "89.00", min_amount="500"),
        ad("upper-bound", QuoteSide.SELL, "88.00", min_amount="2000"),
    ]

    assert select_best_buy_offer(offers).id == "upper-bound"


def test_buy_offer_accepts_sbp_sberbank_and_other_russian_banks() -> None:
    offers = [
        ad("foreign", QuoteSide.SELL, "80.00", payments=("international_transfer",)),
        ad("sber", QuoteSide.SELL, "81.00", payments=("sberbankru", "sbp")),
        ad("vtb", QuoteSide.SELL, "79.00", payments=("vtbbankru",)),
        ad("sbp", QuoteSide.SELL, "88.00", payments=("sbp",)),
    ]

    assert select_best_buy_offer(offers).id == "vtb"


def test_buy_offer_accepts_supported_russian_banks() -> None:
    for payment in ("alfabankru", "gazprombank", "tinkoff", "ozonbank"):
        offer = ad(payment, QuoteSide.SELL, "89.00", payments=(payment,))
        assert select_best_buy_offer([offer]).id == payment


def test_sell_offer_is_highest_online_buy_ad() -> None:
    offers = [ad("low", QuoteSide.BUY, "88.00"), ad("best", QuoteSide.BUY, "90.10")]

    assert select_best_sell_offer(offers).id == "best"


def test_empty_market_is_not_a_zero_price() -> None:
    with pytest.raises(MarketUnavailable):
        select_best_buy_offer([])
