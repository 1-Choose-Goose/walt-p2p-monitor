from decimal import Decimal

import pytest

from walt_price_bot.alerts import evaluate_percent_change, evaluate_threshold
from walt_price_bot.models import (
    AlertKind,
    AlertRule,
    Asset,
    QuoteSide,
    ThresholdDirection,
)


def threshold_rule(*, direction: ThresholdDirection, armed: bool = True) -> AlertRule:
    return AlertRule(
        id=1,
        kind=AlertKind.THRESHOLD,
        asset=Asset.USDT,
        quote_side=QuoteSide.BUY,
        threshold_direction=direction,
        target="90.00",
        baseline=None,
        armed=armed,
        enabled=True,
    )


def percent_rule(*, baseline: str | None = "100.00", target: str = "2.0") -> AlertRule:
    return AlertRule(
        id=2,
        kind=AlertKind.PERCENT,
        asset=Asset.TON,
        quote_side=QuoteSide.SELL,
        threshold_direction=None,
        target=target,
        baseline=baseline,
        armed=True,
        enabled=True,
    )


def test_threshold_notifies_once_then_rearms_after_price_returns() -> None:
    first = evaluate_threshold(threshold_rule(direction=ThresholdDirection.BELOW), Decimal("89.90"))
    repeated = evaluate_threshold(
        threshold_rule(direction=ThresholdDirection.BELOW, armed=False), Decimal("89.50")
    )
    rearmed = evaluate_threshold(
        threshold_rule(direction=ThresholdDirection.BELOW, armed=False), Decimal("90.10")
    )

    assert (first.notify, first.armed) == (True, False)
    assert (repeated.notify, repeated.armed) == (False, False)
    assert (rearmed.notify, rearmed.armed) == (False, True)


def test_above_threshold_uses_inclusive_boundary() -> None:
    result = evaluate_threshold(
        threshold_rule(direction=ThresholdDirection.ABOVE), Decimal("90.00")
    )

    assert result.notify is True


def test_percent_change_reports_both_directions_and_new_baseline() -> None:
    rising = evaluate_percent_change(percent_rule(), Decimal("102.00"))
    falling = evaluate_percent_change(percent_rule(), Decimal("97.50"))

    assert rising.notify is True
    assert rising.change_percent == Decimal("2.00")
    assert rising.next_baseline == Decimal("102.00")
    assert falling.notify is True
    assert falling.change_percent == Decimal("-2.500")


def test_percent_rule_initializes_missing_baseline_without_notification() -> None:
    result = evaluate_percent_change(percent_rule(baseline=None), Decimal("101.25"))

    assert result.notify is False
    assert result.next_baseline == Decimal("101.25")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1"])
def test_alert_calculations_reject_invalid_current_price(value: str) -> None:
    with pytest.raises(ValueError, match="current price"):
        evaluate_threshold(
            threshold_rule(direction=ThresholdDirection.ABOVE), Decimal(value)
        )
    with pytest.raises(ValueError, match="current price"):
        evaluate_percent_change(percent_rule(), Decimal(value))
