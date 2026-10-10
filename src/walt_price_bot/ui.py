from decimal import Decimal, InvalidOperation
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import AlertKind, AlertRule, Asset, MarketSnapshot, P2PAd, ScheduleRule

WALT_URL = "https://t.me/walt"
PAYMENT_NAMES = {
    "dushanbecitybank": "Душанбе Сити Банк",
    "international_transfer": "Международный перевод",
    "sberbank": "Сбербанк",
    "sberbankru": "Сбербанк",
    "sbp": "СБП",
    "tinkoff": "Т-Банк",
    "vtbbankru": "ВТБ",
}


def parse_positive_decimal(value: str, *, maximum: Decimal = Decimal("100")) -> Decimal:
    normalized = value.strip().replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Введите положительное число") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > maximum:
        raise ValueError("Число вне допустимого диапазона")
    return parsed


def parse_daily_time(value: str) -> str:
    stripped = value.strip()
    if len(stripped) != 5 or stripped[2] != ":":
        raise ValueError("Используйте формат ЧЧ:ММ")
    try:
        hour, minute = (int(part) for part in stripped.split(":"))
    except ValueError as exc:
        raise ValueError("Используйте формат ЧЧ:ММ") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Некорректное время")
    return f"{hour:02d}:{minute:02d}"


def format_market_summary(snapshots: list[MarketSnapshot]) -> str:
    blocks = ["📊 <b>Walt P2P — лучшие цены</b>"]
    for snapshot in snapshots:
        blocks.append(
            "\n".join(
                [
                    f"\n<b>{snapshot.asset.value} / RUB</b>",
                    f"🟢 Купить: {_money(snapshot.buy.price)} ₽",
                    _offer_details(snapshot.buy),
                    f"🔵 Продать: {_money(snapshot.sell.price)} ₽",
                    _offer_details(snapshot.sell),
                ]
            )
        )
    available = {snapshot.asset for snapshot in snapshots}
    for asset in Asset:
        if asset not in available:
            blocks.append(f"\n<b>{asset.value} / RUB</b>\nПодходящих онлайн-объявлений сейчас нет.")
    return "\n".join(blocks)


def format_rules(alerts: list[AlertRule], schedules: list[ScheduleRule]) -> str:
    lines = ["🔔 <b>Активные правила</b>"]
    if not alerts and not schedules:
        return "🔕 Активных правил пока нет."
    for rule in alerts:
        action = "покупка" if rule.quote_side.value == "BUY" else "продажа"
        if rule.kind is AlertKind.THRESHOLD:
            sign = (
                "≥"
                if rule.threshold_direction and rule.threshold_direction.value == "above"
                else "≤"
            )
            lines.append(f"A{rule.id}: {rule.asset.value}, {action}, {sign} {rule.target} ₽")
        else:
            lines.append(f"A{rule.id}: {rule.asset.value}, {action}, изменение {rule.target}%")
    for rule in schedules:
        description = (
            f"каждые {rule.interval_hours} ч."
            if rule.interval_hours
            else f"ежедневно в {rule.daily_time}"
        )
        lines.append(f"S{rule.id}: сводка {description}")
    return "\n".join(lines)


def asset_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="USDT", callback_data=f"{prefix}:asset:USDT"),
                InlineKeyboardButton(text="TON", callback_data=f"{prefix}:asset:TON"),
                InlineKeyboardButton(text="Биткоин", callback_data=f"{prefix}:asset:BTC"),
            ]
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💱 Текущие цены", callback_data="menu:price"),
                InlineKeyboardButton(text="🔔 Создать уведомление", callback_data="menu:alert"),
            ],
            [
                InlineKeyboardButton(text="📈 Изменение в процентах", callback_data="menu:change"),
                InlineKeyboardButton(text="🕒 Расписание", callback_data="menu:schedule"),
            ],
            [
                InlineKeyboardButton(text="📋 Мои правила", callback_data="menu:alerts"),
                InlineKeyboardButton(text="🗑 Удалить правило", callback_data="menu:delete"),
            ],
            [
                InlineKeyboardButton(text="⏸ Пауза", callback_data="menu:pause"),
                InlineKeyboardButton(text="▶️ Возобновить", callback_data="menu:resume"),
            ],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help")],
        ]
    )


def side_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Купить", callback_data=f"{prefix}:side:BUY"),
                InlineKeyboardButton(text="Продать", callback_data=f"{prefix}:side:SELL"),
            ]
        ]
    )


def direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Выше или равно", callback_data="alert:direction:above"),
                InlineKeyboardButton(text="Ниже или равно", callback_data="alert:direction:below"),
            ]
        ]
    )


def schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Через N часов", callback_data="schedule:kind:interval"),
                InlineKeyboardButton(text="Ежедневно", callback_data="schedule:kind:daily"),
            ]
        ]
    )


def delete_keyboard(alerts: list[AlertRule], schedules: list[ScheduleRule]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"Удалить A{rule.id}", callback_data=f"delete:alert:{rule.id}")]
        for rule in alerts
    ]
    buttons.extend(
        [
            InlineKeyboardButton(
                text=f"Удалить S{rule.id}", callback_data=f"delete:schedule:{rule.id}"
            )
        ]
        for rule in schedules
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _offer_details(ad: P2PAd) -> str:
    nickname = escape(ad.nickname)
    payments = ", ".join(
        escape(PAYMENT_NAMES.get(payment.lower(), payment)) for payment in ad.payments
    )
    ad_number = escape(ad.number or ad.id)
    return (
        f"   {nickname} · {_money(ad.min_amount)}–{_money(ad.max_amount)} ₽\n"
        f"   {payments or 'способ оплаты не указан'}\n"
        f'   🔗 <a href="{WALT_URL}">Открыть Walt</a> · Объявление №{ad_number}'
    )


def _money(value: Decimal) -> str:
    rendered = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return rendered.removesuffix(",00")
