from dataclasses import dataclass
from decimal import Decimal

from .models import AlertKind, AlertRule, ThresholdDirection


@dataclass(frozen=True, slots=True)
class AlertEvaluation:
    notify: bool
    armed: bool | None = None
    next_baseline: Decimal | None = None
    change_percent: Decimal | None = None


def evaluate_threshold(rule: AlertRule, current_price: Decimal) -> AlertEvaluation:
    if rule.kind is not AlertKind.THRESHOLD or rule.threshold_direction is None:
        raise ValueError("threshold rule required")
    target = Decimal(rule.target)
    crossed = (
        current_price >= target
        if rule.threshold_direction is ThresholdDirection.ABOVE
        else current_price <= target
    )
    if rule.armed and crossed:
        return AlertEvaluation(notify=True, armed=False)
    if not rule.armed and not crossed:
        return AlertEvaluation(notify=False, armed=True)
    return AlertEvaluation(notify=False, armed=rule.armed)


def evaluate_percent_change(rule: AlertRule, current_price: Decimal) -> AlertEvaluation:
    if rule.kind is not AlertKind.PERCENT:
        raise ValueError("percent rule required")
    target = Decimal(rule.target)
    if target <= 0:
        raise ValueError("percent target must be positive")
    if rule.baseline is None:
        return AlertEvaluation(notify=False, next_baseline=current_price)
    baseline = Decimal(rule.baseline)
    if baseline <= 0:
        raise ValueError("percent baseline must be positive")
    change = (current_price - baseline) / baseline * Decimal("100")
    if abs(change) >= target:
        return AlertEvaluation(
            notify=True,
            next_baseline=current_price,
            change_percent=change,
        )
    return AlertEvaluation(
        notify=False,
        next_baseline=baseline,
        change_percent=change,
    )
