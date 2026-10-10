import base64
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .models import (
    SEED_ASSETS,
    AlertKind,
    AlertRule,
    Asset,
    MarketSnapshot,
    OpportunityMode,
    OpportunitySettings,
    P2PAd,
    QuoteSide,
    ScheduleRule,
    UserPreferences,
    UserRole,
)

WALT_MINI_APP_URL = "https://telegram.me/walt/start?startapp="
PAYMENT_NAMES = {
    "akbarsbank": "Ак Барс Банк",
    "alfabank": "Альфа-Банк",
    "alfabankru": "Альфа-Банк",
    "dushanbecitybank": "Душанбе Сити Банк",
    "gazprombank": "Газпромбанк",
    "international_transfer": "Международный перевод",
    "mtsbank": "МТС Банк",
    "ozonbank": "Ozon Банк",
    "raiffeisenbankru": "Райффайзен Банк",
    "rosbank": "Росбанк",
    "rosselkhozbank": "Россельхозбанк",
    "russianstandardbank": "Русский Стандарт",
    "sberbank": "Сбербанк",
    "sberbankru": "Сбербанк",
    "sbp": "СБП",
    "tinkoff": "Т-Банк",
    "vtbbankru": "ВТБ",
}


def parse_positive_decimal(
    value: str,
    *,
    minimum: Decimal = Decimal("0"),
    maximum: Decimal = Decimal("100"),
) -> Decimal:
    normalized = value.strip().replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Введите положительное число") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed < minimum or parsed > maximum:
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


def format_market_summary(
    snapshots: list[MarketSnapshot],
    expected_assets: list[Asset] | tuple[Asset, ...] | set[Asset] | None = None,
    directions: tuple[QuoteSide, ...] = (QuoteSide.BUY, QuoteSide.SELL),
) -> str:
    blocks = ["📊 <b>Walt P2P — актуальные цены</b>"]
    for snapshot in snapshots:
        lines = [f"\n<b>{snapshot.asset.value} / RUB</b>"]
        if snapshot.exchange_price is not None:
            lines.append(f"🏦 Биржевой курс (Coinbase): {_money(snapshot.exchange_price)} ₽")
        if QuoteSide.BUY in directions:
            if not snapshot.buy_source_available:
                lines.append("🟢 Купить: Walt временно недоступен")
            elif snapshot.buy is None:
                lines.append("🟢 Купить: подходящих P2P-объявлений нет")
            else:
                lines.extend(
                    [
                        f"🟢 Купить: {_money(snapshot.buy.price)} ₽",
                        _offer_details(snapshot.buy),
                    ]
                )
        if QuoteSide.SELL in directions:
            if not snapshot.sell_source_available:
                lines.append("🔵 Продать: Walt временно недоступен")
            elif snapshot.sell is None:
                lines.append("🔵 Продать: подходящих P2P-объявлений нет")
            else:
                lines.extend(
                    [
                        f"🔵 Продать: {_money(snapshot.sell.price)} ₽",
                        _offer_details(snapshot.sell),
                    ]
                )
        blocks.append("\n".join(lines))
    available = {snapshot.asset for snapshot in snapshots}
    expected = (
        [entry.symbol for entry in SEED_ASSETS]
        if expected_assets is None
        else expected_assets
    )
    for asset in expected:
        if asset not in available:
            blocks.append(f"\n<b>{asset.value} / RUB</b>\nПодходящих онлайн-объявлений сейчас нет.")
    return "\n".join(blocks)


def format_rules(
    alerts: list[AlertRule],
    schedules: list[ScheduleRule],
    opportunity: OpportunitySettings | None = None,
) -> str:
    lines = ["🔔 <b>Активные правила</b>"]
    if not alerts and not schedules and not (opportunity and opportunity.enabled):
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
    if opportunity and opportunity.enabled:
        mode_names = {
            OpportunityMode.BUY: "покупка",
            OpportunityMode.SELL: "продажа",
            OpportunityMode.BOTH: "покупка и продажа",
        }
        threshold = str(opportunity.min_spread_percent).replace(".", ",")
        lines.append(
            f"💎 Выгодные лоты: {mode_names[opportunity.mode]}, от {threshold}%"
        )
    return "\n".join(lines)


def format_opportunity_settings(settings: OpportunitySettings) -> str:
    if not settings.enabled:
        return (
            "💎 <b>Выгодные лоты</b>\n\n"
            "Автоуведомления выключены. Выберите, какие предложения отслеживать."
        )
    mode_names = {
        OpportunityMode.BUY: "покупка",
        OpportunityMode.SELL: "продажа",
        OpportunityMode.BOTH: "покупка и продажа",
    }
    threshold = str(settings.min_spread_percent).replace(".", ",")
    premium = str(settings.max_buy_premium_percent).replace(".", ",")
    return (
        "💎 <b>Выгодные лоты</b>\n\n"
        "Автоуведомления включены.\n"
        f"Направление: {mode_names[settings.mode]}.\n"
        f"Покупка: любая цена ниже биржи или до {premium}% выше.\n"
        f"Продажа: минимум {threshold}% выше биржи.\n"
        "Сравнение с курсом Coinbase."
    )


def asset_keyboard(prefix: str, entries=SEED_ASSETS) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=entry.symbol.value,
                    callback_data=f"{prefix}:asset:{entry.symbol.value}",
                )
                for entry in entries
            ],
            _back_row(),
        ]
    )


def main_menu_keyboard(
    role: UserRole = UserRole.USER, paused: bool = False
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💱 Текущие цены", callback_data="menu:price")],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="menu:notifications"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings:open:"),
        ],
    ]
    if role is UserRole.ADMIN:
        rows.append(
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="menu:admin")]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def notifications_menu_keyboard(paused: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Выгодные лоты", callback_data="menu:opportunity")],
            [
                InlineKeyboardButton(text="🎯 Ценовой порог", callback_data="menu:alert"),
                InlineKeyboardButton(text="📈 Изменение цены", callback_data="menu:change"),
            ],
            [
                InlineKeyboardButton(text="🕒 Сводки", callback_data="settings:summary:"),
                InlineKeyboardButton(text="📋 Мои правила", callback_data="menu:alerts"),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Категории уведомлений",
                    callback_data="settings:notifications:",
                )
            ],
            [
                InlineKeyboardButton(
                    text="▶️ Возобновить" if paused else "⏸ Приостановить",
                    callback_data="menu:resume" if paused else "menu:pause",
                )
            ],
            _back_row(),
        ]
    )


def summary_settings_keyboard(preferences: UserPreferences) -> InlineKeyboardMarkup:
    current = preferences.summary_interval_minutes if preferences.summary_enabled else None
    choices = (
        ("Каждую 1 минуту", 1),
        ("Каждые 15 минут", 15),
        ("Каждый час", 60),
        ("Каждые 6 часов", 360),
        ("Раз в сутки", 1440),
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if current == minutes else '☐'} {label}",
                callback_data=f"settings:summary_interval:{minutes}",
            )
        ]
        for label, minutes in choices
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=f"{'✅' if current is None else '☐'} Выключить сводки",
                    callback_data="settings:summary_off:",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data="menu:notifications")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def opportunity_mode_keyboard(
    settings: OpportunitySettings | None = None,
) -> InlineKeyboardMarkup:
    spread = settings.min_spread_percent if settings is not None else Decimal("2")
    spread_text = _percent_text(spread)
    premium = (
        settings.max_buy_premium_percent if settings is not None else Decimal("2")
    )
    premium_text = _percent_text(premium)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Покупка", callback_data="opportunity:mode:BUY"
                ),
                InlineKeyboardButton(
                    text="🔵 Продажа", callback_data="opportunity:mode:SELL"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🟢🔵 Покупка и продажа",
                    callback_data="opportunity:mode:BOTH",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📈 Минимум для продажи: {spread_text}%",
                    callback_data="opportunity:threshold-menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📉 Переплата при покупке: до {premium_text}%",
                    callback_data="opportunity:premium-menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Отключить автоуведомления",
                    callback_data="opportunity:disable",
                )
            ],
            _back_row(),
        ]
    )


def opportunity_threshold_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="От 0,5%", callback_data="opportunity:threshold:0.5"),
                InlineKeyboardButton(text="От 1%", callback_data="opportunity:threshold:1"),
            ],
            [
                InlineKeyboardButton(
                    text="От 2% — рекомендуется", callback_data="opportunity:threshold:2"
                ),
                InlineKeyboardButton(text="От 3%", callback_data="opportunity:threshold:3"),
            ],
            [
                InlineKeyboardButton(
                    text="Своя наценка", callback_data="opportunity:threshold:custom"
                )
            ],
            _back_row(),
        ]
    )


def opportunity_premium_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без переплаты", callback_data="opportunity:premium:0"
                ),
                InlineKeyboardButton(
                    text="До 0,5%", callback_data="opportunity:premium:0.5"
                ),
            ],
            [
                InlineKeyboardButton(text="До 1%", callback_data="opportunity:premium:1"),
                InlineKeyboardButton(
                    text="До 2% — рекомендуется",
                    callback_data="opportunity:premium:2",
                ),
            ],
            [
                InlineKeyboardButton(text="До 3%", callback_data="opportunity:premium:3"),
                InlineKeyboardButton(
                    text="Свой процент", callback_data="opportunity:premium:custom"
                ),
            ],
            _back_row(),
        ]
    )


def side_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Купить", callback_data=f"{prefix}:side:BUY"),
                InlineKeyboardButton(text="Продать", callback_data=f"{prefix}:side:SELL"),
            ],
            _back_row(),
        ]
    )


def direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Выше или равно", callback_data="alert:direction:above"),
                InlineKeyboardButton(text="Ниже или равно", callback_data="alert:direction:below"),
            ],
            _back_row(),
        ]
    )


def schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Через N часов", callback_data="schedule:kind:interval"),
                InlineKeyboardButton(text="Ежедневно", callback_data="schedule:kind:daily"),
            ],
            _back_row(),
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
    buttons.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_back_row()])


def _back_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home")]


def _offer_details(ad: P2PAd) -> str:
    nickname = escape(ad.nickname)
    payments = ", ".join(
        escape(PAYMENT_NAMES.get(payment.lower(), payment)) for payment in ad.payments
    )
    ad_number = escape(ad.number or ad.id)
    offer_url = escape(_walt_offer_url(ad), quote=True)
    return (
        f"   {nickname} · {_money(ad.min_amount)}–{_money(ad.max_amount)} ₽\n"
        f"   {payments or 'способ оплаты не указан'}\n"
        f'   🔗 <a href="{offer_url}">Открыть объявление</a> · Объявление №{ad_number}'
    )


def _walt_offer_url(ad: P2PAd) -> str:
    operation = f"operation=offerid_{ad.id}_{ad.user_id}"
    payload = base64.urlsafe_b64encode(operation.encode()).decode().rstrip("=")
    return f"{WALT_MINI_APP_URL}v2-{payload}"


def _money(value: Decimal) -> str:
    rendered = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return rendered.removesuffix(",00")


def _percent_text(value: Decimal) -> str:
    rendered = f"{value:f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
