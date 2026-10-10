import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .access import AccessRequirement
from .messaging import DeleteIncomingMessagesMiddleware
from .models import Asset, QuoteSide, UserPreferences
from .repository import Repository


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
    INTERVAL = "interval"
    SUMMARY_INTERVAL = "summary_interval"
    MONITOR_INTERVAL = "monitor_interval"
    NOTIFICATIONS = "notifications"
    NOTIFICATION_TOGGLE = "notification_toggle"
    QUIET = "quiet"
    QUIET_SET = "quiet_set"
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
                button("🔔 Уведомления", SettingsAction.NOTIFICATIONS),
            ],
            [
                button("🌙 Тихие часы", SettingsAction.QUIET),
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
            directions = {
                "BUY": (QuoteSide.BUY,),
                "SELL": (QuoteSide.SELL,),
                "BOTH": (QuoteSide.BUY, QuoteSide.SELL),
            }[callback_data.value]
            await repository.update_preferences(telegram_id, directions=directions)
            await show_settings(callback)
        elif action is SettingsAction.PAYMENT and callback.message:
            await callback.message.edit_text(
                "🏦 <b>Способы оплаты</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("СБП, Сбербанк и российские банки", SettingsAction.PAYMENT_SET, "banks"),
                        ("Любой способ", SettingsAction.PAYMENT_SET, "any"),
                    ]
                ),
            )
            await callback.answer()
        elif action is SettingsAction.PAYMENT_SET:
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
                "⏱ <b>Как часто обновлять?</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("Проверка цен: 30 сек.", SettingsAction.MONITOR_INTERVAL, "30"),
                        ("Проверка цен: 1 мин.", SettingsAction.MONITOR_INTERVAL, "60"),
                        ("Сводка: 1 мин.", SettingsAction.SUMMARY_INTERVAL, "1"),
                        ("Сводка: 15 мин.", SettingsAction.SUMMARY_INTERVAL, "15"),
                        ("Сводка: 1 ч.", SettingsAction.SUMMARY_INTERVAL, "60"),
                        ("Сводка: 6 ч.", SettingsAction.SUMMARY_INTERVAL, "360"),
                        ("Сводка: 24 ч.", SettingsAction.SUMMARY_INTERVAL, "1440"),
                    ]
                ),
            )
            await callback.answer()
        elif action is SettingsAction.MONITOR_INTERVAL:
            await repository.update_preferences(
                telegram_id, monitor_interval_seconds=int(callback_data.value)
            )
            await show_settings(callback)
        elif action is SettingsAction.SUMMARY_INTERVAL:
            await repository.update_preferences(
                telegram_id,
                summary_interval_minutes=int(callback_data.value),
                summary_enabled=True,
            )
            await show_settings(callback)
        elif action is SettingsAction.NOTIFICATIONS and callback.message:
            await callback.message.edit_text(
                "🔔 <b>Категории уведомлений</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("Ценовые пороги", SettingsAction.NOTIFICATION_TOGGLE, "threshold"),
                        ("Изменения цены", SettingsAction.NOTIFICATION_TOGGLE, "percent"),
                        ("Сводки", SettingsAction.NOTIFICATION_TOGGLE, "scheduled"),
                        ("Выгодные лоты", SettingsAction.NOTIFICATION_TOGGLE, "opportunity"),
                    ]
                ),
            )
            await callback.answer()
        elif action is SettingsAction.NOTIFICATION_TOGGLE:
            current = await repository.get_preferences(telegram_id)
            values = {
                "threshold": current.notifications.threshold,
                "percent": current.notifications.percent,
                "scheduled": current.notifications.scheduled,
                "opportunity": current.notifications.opportunity,
            }
            field = callback_data.value
            await repository.update_preferences(
                telegram_id, **{f"notify_{field}": not values[field]}
            )
            await callback.answer("Сохранено")
        elif action is SettingsAction.QUIET and callback.message:
            await callback.message.edit_text(
                "🌙 <b>Тихие часы</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("Выключить", SettingsAction.QUIET_SET, "off"),
                        ("22:00–08:00", SettingsAction.QUIET_SET, "22:00-08:00"),
                        ("23:00–07:00", SettingsAction.QUIET_SET, "23:00-07:00"),
                    ]
                ),
            )
            await callback.answer()
        elif action is SettingsAction.QUIET_SET:
            if callback_data.value == "off":
                await repository.update_preferences(telegram_id, quiet_enabled=False)
            else:
                start, end = callback_data.value.split("-", 1)
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
        elif action is SettingsAction.LIMIT and not callback_data.value and callback.message:
            await callback.message.edit_text(
                "💵 <b>Допустимый минимальный лимит объявления</b>",
                reply_markup=_choice_keyboard(
                    [
                        ("500–2 000 ₽", SettingsAction.LIMIT, "500:2000"),
                        ("1 000–2 000 ₽", SettingsAction.LIMIT, "1000:2000"),
                        ("2 000 ₽ и выше", SettingsAction.LIMIT, "2000:none"),
                        ("500 ₽ и выше", SettingsAction.LIMIT, "500:none"),
                        ("Другая сумма", SettingsAction.LIMIT, "custom"),
                        ("Сумма сделки: 1 000 ₽", SettingsAction.AMOUNT, "1000"),
                        ("Сумма сделки: 5 000 ₽", SettingsAction.AMOUNT, "5000"),
                        ("Сумма сделки: 25 000 ₽", SettingsAction.AMOUNT, "25000"),
                        ("Не учитывать сумму сделки", SettingsAction.AMOUNT, "none"),
                    ]
                ),
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
            lower, upper = callback_data.value.split(":", 1)
            await repository.update_preferences(
                telegram_id,
                min_limit_from=Decimal(lower),
                min_limit_to=None if upper == "none" else Decimal(upper),
            )
            await show_settings(callback)
        elif action is SettingsAction.AMOUNT:
            await repository.update_preferences(
                telegram_id,
                desired_amount=(
                    None
                    if callback_data.value == "none"
                    else Decimal(callback_data.value)
                ),
            )
            await show_settings(callback)
        else:
            await callback.answer("Раздел будет открыт здесь", show_alert=False)

    @router.message(PreferencesFlow.custom_limit)
    async def custom_limit(message: Message, state: FSMContext) -> None:
        try:
            value = parse_money(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await repository.update_preferences(message.from_user.id, min_limit_from=value)
        await state.clear()
        await message.delete()
        await message.answer("✅ Лимит сохранён.")

    return router
