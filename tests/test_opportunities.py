from decimal import Decimal

import pytest

from walt_price_bot.models import QuoteSide
from walt_price_bot.opportunities import opportunity_is_acceptable, opportunity_percent


def test_buy_opportunity_is_positive_when_p2p_is_below_exchange() -> None:
    assert opportunity_percent(
        QuoteSide.BUY,
        p2p_price=Decimal("95"),
        exchange_price=Decimal("100"),
    ) == Decimal("5.00")


def test_sell_opportunity_is_positive_when_p2p_is_above_exchange() -> None:
    assert opportunity_percent(
        QuoteSide.SELL,
        p2p_price=Decimal("105"),
        exchange_price=Decimal("100"),
    ) == Decimal("5.00")


def test_buy_above_exchange_is_allowed_within_user_premium() -> None:
    difference = opportunity_percent(
        QuoteSide.BUY,
        p2p_price=Decimal("86"),
        exchange_price=Decimal("85"),
    )

    assert opportunity_is_acceptable(
        QuoteSide.BUY,
        difference_percent=difference,
        min_sell_spread_percent=Decimal("2"),
        max_buy_premium_percent=Decimal("2"),
    )
    assert not opportunity_is_acceptable(
        QuoteSide.BUY,
        difference_percent=difference,
        min_sell_spread_percent=Decimal("2"),
        max_buy_premium_percent=Decimal("1"),
    )


def test_opportunity_can_be_negative_and_rejects_invalid_exchange_price() -> None:
    assert opportunity_percent(
        QuoteSide.BUY,
        p2p_price=Decimal("101"),
        exchange_price=Decimal("100"),
    ) == Decimal("-1.00")

    with pytest.raises(ValueError, match="exchange price"):
        opportunity_percent(
            QuoteSide.BUY,
            p2p_price=Decimal("90"),
            exchange_price=Decimal("0"),
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1"])
def test_opportunity_rejects_invalid_p2p_price(value: str) -> None:
    with pytest.raises(ValueError, match="P2P price"):
        opportunity_percent(
            QuoteSide.BUY,
            p2p_price=Decimal(value),
            exchange_price=Decimal("100"),
        )


@pytest.mark.parametrize(
    ("side", "p2p_price", "expected"),
    [
        (QuoteSide.BUY, "99.5", Decimal("0.50")),
        (QuoteSide.SELL, "101", Decimal("1.00")),
        (QuoteSide.BUY, "98", Decimal("2.00")),
        (QuoteSide.SELL, "103", Decimal("3.00")),
    ],
)
def test_opportunity_exact_supported_thresholds(
    side: QuoteSide, p2p_price: str, expected: Decimal
) -> None:
    assert opportunity_percent(
        side, p2p_price=Decimal(p2p_price), exchange_price=Decimal("100")
    ) == expected


@pytest.mark.parametrize(
    ("p2p_price", "exchange_price"),
    [("5E-101", "1E-100"), ("9.9E99", "1E100")],
)
def test_opportunity_handles_very_small_and_large_prices(
    p2p_price: str, exchange_price: str
) -> None:
    result = opportunity_percent(
        QuoteSide.BUY,
        p2p_price=Decimal(p2p_price),
        exchange_price=Decimal(exchange_price),
    )
    assert result.is_finite()
