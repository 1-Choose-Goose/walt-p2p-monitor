from decimal import Decimal

import pytest

from walt_price_bot.market import (
    MarketUnavailable,
    select_best_buy_offer,
    select_best_sell_offer,
    select_buy_offers,
    select_sell_offers,
)
from walt_price_bot.models import Asset, P2PAd, QuoteSide


def ad(
    identifier: str,
    side: QuoteSide,
    price: str,
    *,
    online: bool = True,
    min_amount: str = "1000",
    max_amount: str = "50000",
    payments: tuple[str, ...] = ("sbp",),
) -> P2PAd:
    return P2PAd(
        id=identifier,
        user_id="67890",
        nickname="maker",
        asset=Asset.USDT,
        side=side,
        price=Decimal(price),
        min_amount=Decimal(min_amount),
        max_amount=Decimal(max_amount),
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


def test_buy_candidates_include_every_eligible_unique_ad_in_price_order() -> None:
    offers = [
        ad("middle", QuoteSide.SELL, "89.00"),
        ad("offline", QuoteSide.SELL, "70.00", online=False),
        ad("best", QuoteSide.SELL, "80.00"),
        ad("middle", QuoteSide.SELL, "89.00"),
        ad("wrong-side", QuoteSide.BUY, "95.00"),
        ad("last", QuoteSide.SELL, "91.00"),
    ]

    assert [offer.id for offer in select_buy_offers(offers)] == [
        "best",
        "middle",
        "last",
    ]


def test_sell_candidates_include_every_eligible_unique_ad_in_price_order() -> None:
    offers = [
        ad("low", QuoteSide.BUY, "88.00"),
        ad("best", QuoteSide.BUY, "92.00"),
        ad("middle", QuoteSide.BUY, "90.00"),
        ad("best", QuoteSide.BUY, "92.00"),
    ]

    assert [offer.id for offer in select_sell_offers(offers)] == [
        "best",
        "middle",
        "low",
    ]


def test_sell_offer_requires_minimum_limit_between_500_and_2000_rubles() -> None:
    offers = [
        ad("too-low", QuoteSide.BUY, "105.00", min_amount="499"),
        ad("too-high", QuoteSide.BUY, "104.00", min_amount="2000.01"),
        ad("lower-bound", QuoteSide.BUY, "101.00", min_amount="500"),
        ad("upper-bound", QuoteSide.BUY, "102.00", min_amount="2000"),
    ]

    assert select_best_sell_offer(offers).id == "upper-bound"


def test_empty_market_is_not_a_zero_price() -> None:
    with pytest.raises(MarketUnavailable):
        select_best_buy_offer([])


@pytest.mark.parametrize(
    ("price", "min_amount", "max_amount"),
    [
        ("NaN", "1000", "50000"),
        ("Infinity", "1000", "50000"),
        ("0", "1000", "50000"),
        ("-1", "1000", "50000"),
        ("90", "NaN", "50000"),
        ("90", "2000", "1000"),
    ],
)
def test_market_selection_excludes_invalid_ads(
    price: str, min_amount: str, max_amount: str
) -> None:
    invalid = ad(
        "invalid",
        QuoteSide.SELL,
        price,
        min_amount=min_amount,
        max_amount=max_amount,
    )

    with pytest.raises(MarketUnavailable):
        select_best_buy_offer([invalid])
