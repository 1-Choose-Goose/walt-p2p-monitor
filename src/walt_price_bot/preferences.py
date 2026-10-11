import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .access import AccessRequirement
from .keyboards import SETTINGS_BUTTON
from .messaging import DeleteIncomingMessagesMiddleware, replace_message
from .models import Asset, QuoteSide, UserPreferences
from .onboarding import WELCOME
from .repository import Repository
from .ui import main_menu_keyboard, summary_settings_keyboard


class SettingsAction(StrEnum):
    OPEN = "open"
    ASSETS = "assets"
    ASSET = "asset"
    ALL_ASSETS = "all_assets"
    LIMIT = "limit"
    AMOUNT = "amount"
    DIRECTIONS = "directions"
    DIRECTION = "direction"
    PAYMENT = "payment"
    PAYMENT_SET = "payment_set"
    PAYMENT_TOGGLE = "payment_toggle"
    INTERVAL = "interval"
    SUMMARY = "summary"
    SUMMARY_INTERVAL = "summary_interval"
    SUMMARY_OFF = "summary_off"
    MONITOR_INTERVAL = "monitor_interval"
    NOTIFICATIONS = "notifications"
    NOTIFICATION_TOGGLE = "notification_toggle"
    QUIET = "quiet"
    QUIET_SET = "quiet_set"
    QUIET_CUSTOM = "quiet_custom"
    QUIET_START_HOUR = "quiet_start_hour"
    QUIET_START_MINUTE = "quiet_start_minute"
    QUIET_END_HOUR = "quiet_end_hour"
    QUIET_END_MINUTE = "quiet_end_minute"
    EXCHANGE = "exchange"
    PAUSE = "pause"
    RESET = "reset"
    HOME = "home"


class SettingsCallback(CallbackData, prefix="settings"):
    action: SettingsAction
    value: str = ""


class PreferencesFlow(StatesGroup):
    custom_limit = State()
    desired_amount = State()
    quiet_custom = State()


PAYMENT_METHODS = (
    ("СБП", "sbp"),
    ("Сбербанк", "sberbankru"),
    ("Т-Банк", "tinkoff"),
    ("Альфа-Банк", "alfabankru"),
    ("ВТБ", "vtbbankru"),
    ("Райффайзен Банк", "raiffeisenbankru"),
    ("Ozon Банк", "ozonbank"),
    ("Газпромбанк", "gazprombank"),
    ("МТС Банк", "mtsbank"),
    ("Россельхозбанк", "rosselkhozbank"),
    ("Росбанк", "rosbank"),
    ("Русский Стандарт", "russianstandardbank"),
    ("Ак Барс Банк", "akbarsbank"),
)
PAYMENT_LABELS = dict(PAYMENT_METHODS) | {
    "Все российские банки": "russian_banks",
    "Любой способ": "any",
}
PAYMENT_NAMES_BY_CODE = {code: label for label, code in PAYMENT_LABELS.items()}


def parse_money(text: str) -> Decimal:
    normalized = re.sub(r"[\s₽]", "", text).replace(",", ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Введите сумму цифрами") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return value


def summary_interval_minutes(hours: int, minutes: int) -> int:
    total = hours * 60 + minutes
    if hours < 0 or minutes < 0 or minutes > 59 or not 1 <= total <= 1440:
        raise ValueError("Интервал должен быть от 1 минуты до 24 часов")
    return total


def _money(value: Decimal | None) -> str:
    if value is None:
        return "без ограничения"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def format_preferences(preferences: UserPreferences) -> str:
    directions = {
        (QuoteSide.BUY,): "покупка",
        (QuoteSide.SELL,): "продажа",
        (QuoteSide.BUY, QuoteSide.SELL): "покупка и продажа",
    }.get(preferences.directions, ", ".join(item.value for item in preferences.directions))
    interval = preferences.summary_interval_minutes
    summary = (
        f"каждую {interval} мин."
        if interval < 60
        else f"каждые {interval // 60} ч. {interval % 60} мин."
    )
    desired_amount = (
        _money(preferences.desired_amount)
        if preferences.desired_amount
        else "не задана"
    )
    quiet_hours = (
        f"{preferences.quiet_hours.start}–{preferences.quiet_hours.end}"
        if preferences.quiet_hours.enabled
        else "выключены"
    )
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"Монеты: {', '.join(asset.value for asset in preferences.assets)}\n"
        f"Направления: {directions}\n"
        f"Лимит объявления: {_money(preferences.min_limit_from)} — "
        f"{_money(preferences.min_limit_to)}\n"
        f"Сумма сделки: {desired_amount}\n"
        f"Проверка цен: каждые {preferences.monitor_interval_seconds} сек.\n"
        f"Сводка: {summary if preferences.summary_enabled else 'выключена'}\n"
        f"Биржевой курс: {'показывать' if preferences.show_exchange_rate else 'скрывать'}\n"
        f"Тихие часы: {quiet_hours}"
    )


async def toggle_asset(
    repository: Repository, telegram_id: int, asset: Asset
) -> tuple[Asset, ...]:
    selected = list(await repository.get_user_assets(telegram_id))
    if asset in selected:
        if len(selected) == 1:
            raise ValueError("Нужно выбрать хотя бы одну монету")
        selected.remove(asset)
    else:
        selected.append(asset)
    ordered = tuple(
        entry.symbol
        for entry in await repository.list_assets()
        if entry.symbol in selected
    )
    await repository.replace_user_assets(telegram_id, ordered)
    return ordered


def settings_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    def button(text: str, action: SettingsAction, value: str = "") -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text, callback_data=SettingsCallback(action=action, value=value).pack()
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("🪙 Монеты", SettingsAction.ASSETS),
                button("↕️ Направления", SettingsAction.DIRECTIONS),
            ],
            [
                button("💵 Лимиты и сумма", SettingsAction.LIMIT),
                button("🏦 Способы оплаты", SettingsAction.PAYMENT),
            ],
            [
                button("⏱ Интервалы", SettingsAction.INTERVAL),
                button("🌙 Тихие часы", SettingsAction.QUIET),
            ],
            [
                button("🏦 Биржевой курс", SettingsAction.EXCHANGE),
            ],
            [
                button(
                    "🔕 Пауза" if not paused else "🔔 Возобновить",
                    SettingsAction.PAUSE,
                )
            ],
            [button("↩️ Сбросить настройки", SettingsAction.RESET)],
            [button("🏠 Главное меню", SettingsAction.HOME)],
        ]
    )


def _choice_keyboard(
    choices: list[tuple[str, SettingsAction, str]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=text,
                callback_data=SettingsCallback(action=action, value=value).pack(),
            )
        ]
        for text, action, value in choices
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=SettingsCallback(action=SettingsAction.OPEN).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_categories_keyboard(
    preferences: UserPreferences,
) -> InlineKeyboardMarkup:
    choices = [
        ("Ценовые пороги", "threshold", preferences.notifications.threshold),
        ("Изменения цены", "percent", preferences.notifications.percent),
        ("Выгодные лоты", "opportunity", preferences.notifications.opportunity),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if enabled else '☐'} {label}",
                callback_data=SettingsCallback(
                    action=SettingsAction.NOTIFICATION_TOGGLE,
                    value=value,
                ).pack(),
            )
        ]
        for label, value, enabled in choices
    ]
    rows.append(
        [InlineKeyboardButton(text="Назад", callback_data="menu:notifications")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_methods_keyboard(preferences: UserPreferences) -> InlineKeyboardMarkup:
    selected = set(preferences.buy_payments)
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅' if code in selected else '☐'} {label}",
            callback_data=SettingsCallback(
                action=SettingsAction.PAYMENT_TOGGLE,
                value=code,
            ).pack(),
        )
        for label, code in PAYMENT_LABELS.items()
    ]
    specific_count = len(PAYMENT_METHODS)
    rows = [
        buttons[index : index + 2]
        for index in range(0, specific_count, 2)
    ]
    rows.extend([[button] for button in buttons[specific_count:]])
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=SettingsCallback(action=SettingsAction.OPEN).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def toggle_payment_method(
    repository: Repository,
    telegram_id: int,
    method: str,
) -> tuple[str, ...]:
    if method not in PAYMENT_NAMES_BY_CODE:
        raise ValueError("Неизвестный способ оплаты")
    current = set((await repository.get_preferences(telegram_id)).buy_payments)
    if method in {"any", "russian_banks"}:
        selected = (method,)
    else:
        broad_selection = bool(current & {"any", "russian_banks"})
        current.discard("any")
        current.discard("russian_banks")
        if broad_selection:
            current = {method}
        elif method in current:
            current.remove(method)
        else:
            current.add(method)
        if not current:
            raise ValueError("Нужно оставить хотя бы один способ оплаты")
        selected = tuple(code for _, code in PAYMENT_METHODS if code in current)
    await repository.update_preferences(
        telegram_id,
        buy_payments=selected,
        sell_payments=selected,
    )
    return selected


def limit_settings_keyboard() -> InlineKeyboardMarkup:
    return _choice_keyboard(
        [
            ("500–2 000 ₽", SettingsAction.LIMIT, "500-2000"),
            ("1 000–2 000 ₽", SettingsAction.LIMIT, "1000-2000"),
            ("2 000 ₽ и выше", SettingsAction.LIMIT, "2000-none"),
            ("500 ₽ и выше", SettingsAction.LIMIT, "500-none"),
            ("Другая сумма", SettingsAction.LIMIT, "custom"),
            ("Сумма сделки: 1 000 ₽", SettingsAction.AMOUNT, "1000"),
            ("Сумма сделки: 5 000 ₽", SettingsAction.AMOUNT, "5000"),
            ("Сумма сделки: 25 000 ₽", SettingsAction.AMOUNT, "25000"),
            ("Не учитывать сумму сделки", SettingsAction.AMOUNT, "none"),
        ]
    )


def time_picker_keyboard(
    action: SettingsAction,
    values: Iterable[int],
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{value:02d}",
            callback_data=SettingsCallback(action=action, value=f"{value:02d}").pack(),
        )
        for value in values
    ]
    rows = [buttons[index : index + 6] for index in range(0, len(buttons), 6)]
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=SettingsCallback(action=SettingsAction.QUIET).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def return_to_main_menu(
    callback: CallbackQuery, state: FSMContext, repository: Repository
) -> None:
    await state.clear()
    if callback.message:
        account = await repository.get_user(callback.from_user.id)
        if account is None:
            raise ValueError("unknown user")
        await callback.message.edit_text(
            WELCOME,
            reply_markup=main_menu_keyboard(
                account.role,
                await repository.is_paused(callback.from_user.id),
            ),
        )
    await callback.answer()


async def show_quiet_hours(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(
            "🌙 <b>Тихие часы</b>",
            reply_markup=_choice_keyboard(
                [
                    ("Выключить", SettingsAction.QUIET_SET, "off"),
                    ("22:00–08:00", SettingsAction.QUIET_SET, "2200-0800"),
                    ("23:00–07:00", SettingsAction.QUIET_SET, "2300-0700"),
                    ("⚙️ Свое время", SettingsAction.QUIET_CUSTOM, ""),
                ]
            ),
        )
    await callback.answer()


async def restart_quiet_hours_setup(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await state.set_state(PreferencesFlow.quiet_custom)
    if callback.message:
        await callback.message.edit_text(
            "🌙 <b>Начало тихих часов</b>\n\nВыберите час:",
            reply_markup=time_picker_keyboard(
                SettingsAction.QUIET_START_HOUR, range(24)
            ),
        )
    await callback.answer(
        "Настройка устарела. Начните выбор времени заново.",
        show_alert=True,
    )


async def reject_stale_setting(callback: CallbackQuery) -> None:
    await callback.answer(
        "Эта кнопка устарела. Откройте раздел заново.", show_alert=True
    )


async def assets_keyboard(repository: Repository, telegram_id: int) -> InlineKeyboardMarkup:
    selected = set(await repository.get_user_assets(telegram_id))
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if entry.symbol in selected else '▫️'} {entry.symbol.value}",
                callback_data=SettingsCallback(
                    action=SettingsAction.ASSET, value=entry.symbol.value
                ).pack(),
            )
        ]
        for entry in await repository.list_assets()
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Выбрать все",
                callback_data=SettingsCallback(action=SettingsAction.ALL_ASSETS).pack(),
            ),
            InlineKeyboardButton(
                text="Назад",
                callback_data=SettingsCallback(action=SettingsAction.OPEN).pack(),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_preferences_router(repository: Repository) -> Router:
    router = Router(name="preferences")
    router.message.outer_middleware(DeleteIncomingMessagesMiddleware())

    async def show_settings(event: Message | CallbackQuery) -> None:
        telegram_id = event.from_user.id
        text = format_preferences(await repository.get_preferences(telegram_id))
        markup = settings_keyboard(await repository.is_paused(telegram_id))
        if isinstance(event, CallbackQuery) and event.message:
            await event.message.edit_text(text, reply_markup=markup)
            await event.answer()
        else:
            await event.answer(text, reply_markup=markup)

    @router.message(
        F.text == SETTINGS_BUTTON, flags={"access": AccessRequirement.ACTIVE}
    )
    @router.message(Command("settings"), flags={"access": AccessRequirement.ACTIVE})
    async def settings(message: Message) -> None:
        await show_settings(message)

    @router.callback_query(SettingsCallback.filter())
    async def setting_action(
        callback: CallbackQuery, callback_data: SettingsCallback, state: FSMContext
    ) -> None:
        telegram_id = callback.from_user.id
        action = callback_data.action
        if action is SettingsAction.OPEN:
            await show_settings(callback)
        elif action is SettingsAction.ASSETS and callback.message:
            await callback.message.edit_text(
                "🪙 <b>Какие монеты отслеживать?</b>",
                reply_markup=await assets_keyboard(repository, telegram_id),
            )
            await callback.answer()
        elif action is SettingsAction.ASSET:
            try:
                await toggle_asset(repository, telegram_id, Asset(callback_data.value))
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            if callback.message:
                await callback.message.edit_reply_markup(
                    reply_markup=await assets_keyboard(repository, telegram_id)
                )
            await callback.answer("Сохранено")
        elif action is SettingsAction.ALL_ASSETS:
            assets = tuple(entry.symbol for entry in await repository.list_assets())
            await repository.replace_user_assets(telegram_id, assets)
            if callback.message:
                await callback.message.edit_reply_markup(
                    reply_markup=await assets_keyboard(repository, telegram_id)
                )
            await callback.answer("Выбраны все монеты")
        elif action is SettingsAction.DIRECTIONS and callback.message:
            await callback.message.edit_text(
                "↕️ <b>Что отслеживать?</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("Покупку", SettingsAction.DIRECTION, "BUY"),
                        ("Продажу", SettingsAction.DIRECTION, "SELL"),
                        ("Покупку и продажу", SettingsAction.DIRECTION, "BOTH"),
                    ]
                ),
            )
            await callback.answer()
        elif action is SettingsAction.DIRECTION:
            direction_choices = {
                "BUY": (QuoteSide.BUY,),
                "SELL": (QuoteSide.SELL,),
                "BOTH": (QuoteSide.BUY, QuoteSide.SELL),
            }
            directions = direction_choices.get(callback_data.value)
            if directions is None:
                await reject_stale_setting(callback)
                return
            await repository.update_preferences(telegram_id, directions=directions)
            await show_settings(callback)
        elif action is SettingsAction.PAYMENT and callback.message:
            await callback.message.edit_text(
                "🏦 <b>Способы оплаты</b>\n\n"
                "✅ — учитывать, ☐ — не учитывать.",
                reply_markup=payment_methods_keyboard(
                    await repository.get_preferences(telegram_id)
                ),
            )
            await callback.answer()
        elif action is SettingsAction.PAYMENT_TOGGLE:
            try:
                selected = await toggle_payment_method(
                    repository, telegram_id, callback_data.value
                )
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            if callback.message:
                await callback.message.edit_reply_markup(
                    reply_markup=payment_methods_keyboard(
                        await repository.get_preferences(telegram_id)
                    )
                )
            if callback_data.value == "any":
                status = "Разрешён любой способ оплаты"
            elif callback_data.value == "russian_banks":
                status = "Выбраны все российские банки"
            else:
                enabled = callback_data.value in selected
                status = (
                    f"{PAYMENT_NAMES_BY_CODE[callback_data.value]}: "
                    f"{'включён' if enabled else 'выключен'}"
                )
            await callback.answer(status)
        elif action is SettingsAction.PAYMENT_SET:
            if callback_data.value not in {"banks", "any"}:
                await reject_stale_setting(callback)
                return
            payments = (
                ("sbp", "sberbankru", "russian_banks")
                if callback_data.value == "banks"
                else ("any",)
            )
            await repository.update_preferences(
                telegram_id, buy_payments=payments, sell_payments=payments
            )
            await show_settings(callback)
        elif action is SettingsAction.INTERVAL and callback.message:
            await callback.message.edit_text(
                "⏱ <b>Как часто проверять цены?</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("Проверка цен: 30 сек.", SettingsAction.MONITOR_INTERVAL, "30"),
                        ("Проверка цен: 1 мин.", SettingsAction.MONITOR_INTERVAL, "60"),
                    ]
                ),
            )
            await callback.answer()
        elif action is SettingsAction.MONITOR_INTERVAL:
            if callback_data.value not in {"30", "60"}:
                await reject_stale_setting(callback)
                return
            await repository.update_preferences(
                telegram_id, monitor_interval_seconds=int(callback_data.value)
            )
            await show_settings(callback)
        elif action is SettingsAction.SUMMARY and callback.message:
            await callback.message.edit_text(
                "🕒 <b>Сводки</b>\n\nВыберите частоту или отключите сводки.",
                reply_markup=summary_settings_keyboard(
                    await repository.get_preferences(telegram_id)
                ),
            )
            await callback.answer()
        elif action is SettingsAction.SUMMARY_INTERVAL:
            try:
                summary_minutes = int(callback_data.value)
            except ValueError:
                await reject_stale_setting(callback)
                return
            if not 1 <= summary_minutes <= 1440:
                await reject_stale_setting(callback)
                return
            await repository.update_preferences(
                telegram_id,
                summary_interval_minutes=summary_minutes,
                summary_enabled=True,
                notify_scheduled=True,
            )
            if callback.message:
                await callback.message.edit_text(
                    "🕒 <b>Сводки</b>\n\nЧастота сохранена.",
                    reply_markup=summary_settings_keyboard(
                        await repository.get_preferences(telegram_id)
                    ),
                )
            await callback.answer("Частота сводок изменена")
        elif action is SettingsAction.SUMMARY_OFF:
            await repository.update_preferences(
                telegram_id,
                summary_enabled=False,
                notify_scheduled=False,
            )
            if callback.message:
                await callback.message.edit_text(
                    "🕒 <b>Сводки</b>\n\nСводки выключены.",
                    reply_markup=summary_settings_keyboard(
                        await repository.get_preferences(telegram_id)
                    ),
                )
            await callback.answer("Сводки выключены")
        elif action is SettingsAction.NOTIFICATIONS and callback.message:
            await callback.message.edit_text(
                "🔔 <b>Категории уведомлений</b>\n\n"
                "✅ — включено, ☐ — выключено.\n"
                "Нажмите на категорию, чтобы изменить её состояние.",
                reply_markup=notification_categories_keyboard(
                    await repository.get_preferences(telegram_id)
                ),
            )
            await callback.answer()
        elif action is SettingsAction.NOTIFICATION_TOGGLE:
            current = await repository.get_preferences(telegram_id)
            values = {
                "threshold": current.notifications.threshold,
                "percent": current.notifications.percent,
                "opportunity": current.notifications.opportunity,
            }
            field = callback_data.value
            if field not in values:
                await reject_stale_setting(callback)
                return
            enabled = not values[field]
            await repository.update_preferences(
                telegram_id, **{f"notify_{field}": enabled}
            )
            if callback.message:
                await callback.message.edit_reply_markup(
                    reply_markup=notification_categories_keyboard(
                        await repository.get_preferences(telegram_id)
                    )
                )
            labels = {
                "threshold": "Ценовые пороги",
                "percent": "Изменения цены",
                "opportunity": "Выгодные лоты",
            }
            await callback.answer(
                f"{labels[field]}: {'включены' if enabled else 'выключены'}"
            )
        elif action is SettingsAction.QUIET:
            await show_quiet_hours(callback)
        elif action is SettingsAction.QUIET_CUSTOM:
            await state.clear()
            await state.set_state(PreferencesFlow.quiet_custom)
            if callback.message:
                await callback.message.edit_text(
                    "🌙 <b>Начало тихих часов</b>\n\nВыберите час:",
                    reply_markup=time_picker_keyboard(
                        SettingsAction.QUIET_START_HOUR, range(24)
                    ),
                )
            await callback.answer()
        elif action is SettingsAction.QUIET_START_HOUR:
            if callback_data.value not in {f"{hour:02d}" for hour in range(24)}:
                await reject_stale_setting(callback)
                return
            await state.update_data(quiet_start_hour=callback_data.value)
            if callback.message:
                await callback.message.edit_text(
                    f"🌙 <b>Начало: {callback_data.value}:__</b>\n\n"
                    "Выберите минуты:",
                    reply_markup=time_picker_keyboard(
                        SettingsAction.QUIET_START_MINUTE, (0, 15, 30, 45)
                    ),
                )
            await callback.answer()
        elif action is SettingsAction.QUIET_START_MINUTE:
            if callback_data.value not in {"00", "15", "30", "45"}:
                await reject_stale_setting(callback)
                return
            data = await state.get_data()
            start_hour = data.get("quiet_start_hour")
            if start_hour is None:
                await restart_quiet_hours_setup(callback, state)
                return
            start = f"{start_hour}:{callback_data.value}"
            await state.update_data(quiet_start=start)
            if callback.message:
                await callback.message.edit_text(
                    f"🌙 <b>Начало: {start}</b>\n\n"
                    "Теперь выберите час окончания:",
                    reply_markup=time_picker_keyboard(
                        SettingsAction.QUIET_END_HOUR, range(24)
                    ),
                )
            await callback.answer()
        elif action is SettingsAction.QUIET_END_HOUR:
            if callback_data.value not in {f"{hour:02d}" for hour in range(24)}:
                await reject_stale_setting(callback)
                return
            await state.update_data(quiet_end_hour=callback_data.value)
            if callback.message:
                await callback.message.edit_text(
                    f"🌙 <b>Окончание: {callback_data.value}:__</b>\n\n"
                    "Выберите минуты:",
                    reply_markup=time_picker_keyboard(
                        SettingsAction.QUIET_END_MINUTE, (0, 15, 30, 45)
                    ),
                )
            await callback.answer()
        elif action is SettingsAction.QUIET_END_MINUTE:
            if callback_data.value not in {"00", "15", "30", "45"}:
                await reject_stale_setting(callback)
                return
            data = await state.get_data()
            start_value = data.get("quiet_start")
            end_hour = data.get("quiet_end_hour")
            if start_value is None or end_hour is None:
                await restart_quiet_hours_setup(callback, state)
                return
            start = str(start_value)
            end = f"{end_hour}:{callback_data.value}"
            if start == end:
                await callback.answer(
                    "Начало и окончание не должны совпадать.",
                    show_alert=True,
                )
                return
            await repository.update_preferences(
                telegram_id,
                quiet_enabled=True,
                quiet_start=start,
                quiet_end=end,
            )
            await state.clear()
            await show_settings(callback)
        elif action is SettingsAction.QUIET_SET:
            await state.clear()
            if callback_data.value == "off":
                await repository.update_preferences(telegram_id, quiet_enabled=False)
            else:
                if not re.fullmatch(r"\d{4}-\d{4}", callback_data.value):
                    await reject_stale_setting(callback)
                    return
                start, end = callback_data.value.split("-", 1)
                start = f"{start[:2]}:{start[2:]}"
                end = f"{end[:2]}:{end[2:]}"
                if not (
                    re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start)
                    and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end)
                ):
                    await reject_stale_setting(callback)
                    return
                await repository.update_preferences(
                    telegram_id, quiet_enabled=True, quiet_start=start, quiet_end=end
                )
            await show_settings(callback)
        elif action is SettingsAction.EXCHANGE:
            current = await repository.get_preferences(telegram_id)
            await repository.update_preferences(
                telegram_id, show_exchange_rate=not current.show_exchange_rate
            )
            await show_settings(callback)
        elif action is SettingsAction.PAUSE:
            paused = not await repository.is_paused(telegram_id)
            await repository.set_paused(telegram_id, paused)
            await show_settings(callback)
        elif action is SettingsAction.RESET:
            await repository.reset_preferences(telegram_id)
            await show_settings(callback)
        elif action is SettingsAction.HOME:
            await return_to_main_menu(callback, state, repository)
        elif action is SettingsAction.LIMIT and not callback_data.value and callback.message:
            await callback.message.edit_text(
                "💵 <b>Допустимый минимальный лимит объявления</b>",
                reply_markup=limit_settings_keyboard(),
            )
            await callback.answer()
        elif action is SettingsAction.LIMIT and callback_data.value == "custom":
            await state.set_state(PreferencesFlow.custom_limit)
            await callback.answer()
            if callback.message:
                await callback.message.edit_text(
                    "Введите нижнюю границу минимального лимита в рублях."
                )
        elif action is SettingsAction.LIMIT:
            try:
                lower, upper = callback_data.value.split("-", 1)
                await repository.update_preferences(
                    telegram_id,
                    min_limit_from=Decimal(lower),
                    min_limit_to=None if upper == "none" else Decimal(upper),
                )
            except (InvalidOperation, ValueError):
                await reject_stale_setting(callback)
                return
            await show_settings(callback)
        elif action is SettingsAction.AMOUNT:
            try:
                amount = (
                    None
                    if callback_data.value == "none"
                    else Decimal(callback_data.value)
                )
                await repository.update_preferences(
                    telegram_id, desired_amount=amount
                )
            except (InvalidOperation, ValueError):
                await reject_stale_setting(callback)
                return
            await show_settings(callback)
        else:
            await callback.answer("Раздел будет открыт здесь", show_alert=False)

    @router.message(PreferencesFlow.custom_limit)
    async def custom_limit(message: Message, state: FSMContext) -> None:
        try:
            value = parse_money(message.text or "")
            await repository.update_preferences(
                message.from_user.id, min_limit_from=value
            )
        except ValueError as exc:
            await replace_message(message, repository, str(exc))
            return
        await state.clear()
        await message.delete()
        account = await repository.get_user(message.from_user.id)
        if account is None:
            raise ValueError("unknown user")
        await replace_message(
            message,
            repository,
            "✅ Лимит сохранён.",
            reply_markup=main_menu_keyboard(
                account.role,
                await repository.is_paused(message.from_user.id),
            ),
        )

    return router
