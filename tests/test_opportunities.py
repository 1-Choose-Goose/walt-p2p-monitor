from decimal import Decimal

import pytest

from walt_price_bot.models import QuoteSide
from walt_price_bot.opportunities import opportunity_percent


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
