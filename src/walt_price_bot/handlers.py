from decimal import Decimal
from typing import Any, Protocol

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .messaging import DeleteIncomingMessagesMiddleware, replace_message
from .models import (
    AlertKind,
    Asset,
    MarketSnapshot,
    QuoteSide,
    ScheduleKind,
    ThresholdDirection,
)
from .repository import Repository
from .ui import (
    asset_keyboard,
    back_keyboard,
    delete_keyboard,
    direction_keyboard,
    format_market_summary,
    format_rules,
    main_menu_keyboard,
    parse_daily_time,
    parse_positive_decimal,
    schedule_keyboard,
    side_keyboard,
)

WELCOME = (
    "👋 <b>Walt P2P — монитор цен</b>\n\n"
    "Слежу за лучшими ценами USDT/RUB, TON/RUB и BTC/RUB.\n"
    "Для покупки учитываю объявления с минимальным лимитом от 500 до 2 000 ₽.\n"
    "Оплата: СБП, Сбербанк или другой российский банк.\n"
    "Используйте /price, /alert, /change или /schedule.\n"
    "/start — вернуться в главное меню."
)

HELP = (
    "<b>Команды</b>\n"
    "/start — главное меню\n"
    "/price — текущие цены\n"
    "/alert — ценовой порог\n"
    "/change — изменение в процентах\n"
    "/schedule — регулярная сводка\n"
    "/alerts — все правила\n"
    "/delete — удалить правило\n"
    "/pause и /resume — остановить или возобновить уведомления"
)


class MarketProvider(Protocol):
    async def get_snapshots(self) -> list[MarketSnapshot]: ...


class AlertFlow(StatesGroup):
    asset = State()
    side = State()
    direction = State()
    target = State()


class ChangeFlow(StatesGroup):
    asset = State()
    side = State()
    percent = State()


class ScheduleFlow(StatesGroup):
    kind = State()
    value = State()


async def owner_required(event: Message | CallbackQuery | Any, repository: Repository) -> bool:
    user = getattr(event, "from_user", None)
    if user is not None and await repository.is_owner(user.id):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer("Этот бот доступен только владельцу.", show_alert=True)
    else:
        await event.answer("⛔ Этот бот доступен только владельцу.")
    return False


async def handle_start(message: Message | Any, repository: Repository) -> None:
    user = getattr(message, "from_user", None)
    if user is None or not await repository.claim_owner(user.id):
        await message.answer("⛔ Этот бот доступен только владельцу.")
        return
    await replace_message(message, repository, WELCOME, reply_markup=main_menu_keyboard())


def build_router(repository: Repository, market_provider: MarketProvider) -> Router:
    router = Router(name=__name__)
    router.message.outer_middleware(DeleteIncomingMessagesMiddleware())

    @router.message(Command("start"))
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await handle_start(message, repository)

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if await owner_required(message, repository):
            await replace_message(message, repository, HELP, reply_markup=main_menu_keyboard())

    @router.message(Command("price"))
    async def price_command(message: Message) -> None:
        if not await owner_required(message, repository):
            return
        try:
            snapshots = await market_provider.get_snapshots()
        except Exception:
            await replace_message(
                message,
                repository,
                "⚠️ Не удалось получить цены Walt. Повторите немного позже.",
                reply_markup=main_menu_keyboard(),
            )
            return
        await replace_message(
            message,
            repository,
            format_market_summary(snapshots),
            reply_markup=main_menu_keyboard(),
        )

    @router.message(Command("alerts"))
    async def alerts_command(message: Message) -> None:
        if await owner_required(message, repository):
            await replace_message(
                message,
                repository,
                format_rules(await repository.list_alerts(), await repository.list_schedules()),
                reply_markup=main_menu_keyboard(),
            )

    @router.message(Command("pause"))
    async def pause_command(message: Message) -> None:
        if await owner_required(message, repository):
            await repository.set_paused(True)
            await replace_message(
                message,
                repository,
                "⏸ Уведомления приостановлены.",
                reply_markup=main_menu_keyboard(),
            )

    @router.message(Command("resume"))
    async def resume_command(message: Message) -> None:
        if await owner_required(message, repository):
            await repository.set_paused(False)
            await replace_message(
                message,
                repository,
                "▶️ Уведомления возобновлены.",
                reply_markup=main_menu_keyboard(),
            )

    @router.message(Command("alert"))
    async def alert_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(AlertFlow.asset)
            await replace_message(
                message, repository, "Выберите монету:", reply_markup=asset_keyboard("alert")
            )

    @router.callback_query(AlertFlow.asset, F.data.startswith("alert:asset:"))
    async def alert_asset(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(asset=callback.data.rsplit(":", 1)[1])
        await state.set_state(AlertFlow.side)
        await _callback_message(callback, "Какую цену отслеживать?", side_keyboard("alert"))

    @router.callback_query(AlertFlow.side, F.data.startswith("alert:side:"))
    async def alert_side(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(side=callback.data.rsplit(":", 1)[1])
        await state.set_state(AlertFlow.direction)
        await _callback_message(callback, "Когда уведомить?", direction_keyboard())

    @router.callback_query(AlertFlow.direction, F.data.startswith("alert:direction:"))
    async def alert_direction(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(direction=callback.data.rsplit(":", 1)[1])
        await state.set_state(AlertFlow.target)
        await _callback_message(
            callback, "Введите цену в рублях, например 90,50:", back_keyboard()
        )

    @router.message(AlertFlow.target)
    async def alert_target(message: Message, state: FSMContext) -> None:
        if not await owner_required(message, repository):
            return
        try:
            target = parse_positive_decimal(message.text or "", maximum=Decimal("1000000000"))
        except ValueError:
            await replace_message(
                message,
                repository,
                "Введите корректную положительную цену в рублях.",
                reply_markup=back_keyboard(),
            )
            return
        data = await state.get_data()
        alert_id = await repository.add_alert(
            kind=AlertKind.THRESHOLD,
            asset=Asset(data["asset"]),
            quote_side=QuoteSide(data["side"]),
            threshold_direction=ThresholdDirection(data["direction"]),
            target=str(target),
        )
        await state.clear()
        await replace_message(
            message,
            repository,
            f"✅ Ценовое уведомление A{alert_id} создано.",
            reply_markup=main_menu_keyboard(),
        )

    @router.message(Command("change"))
    async def change_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(ChangeFlow.asset)
            await replace_message(
                message, repository, "Выберите монету:", reply_markup=asset_keyboard("change")
            )

    @router.callback_query(ChangeFlow.asset, F.data.startswith("change:asset:"))
    async def change_asset(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(asset=callback.data.rsplit(":", 1)[1])
        await state.set_state(ChangeFlow.side)
        await _callback_message(callback, "Какую цену отслеживать?", side_keyboard("change"))

    @router.callback_query(ChangeFlow.side, F.data.startswith("change:side:"))
    async def change_side(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(side=callback.data.rsplit(":", 1)[1])
        await state.set_state(ChangeFlow.percent)
        await _callback_message(
            callback, "Введите процент изменения от 0,1 до 100:", back_keyboard()
        )

    @router.message(ChangeFlow.percent)
    async def change_percent(message: Message, state: FSMContext) -> None:
        if not await owner_required(message, repository):
            return
        try:
            percent = parse_positive_decimal(message.text or "", maximum=Decimal("100"))
            data = await state.get_data()
            asset = Asset(data["asset"])
            side = QuoteSide(data["side"])
            snapshot = next(
                item for item in await market_provider.get_snapshots() if item.asset is asset
            )
            offer = snapshot.buy if side is QuoteSide.BUY else snapshot.sell
            if offer is None:
                raise ValueError
            baseline = offer.price
        except (ValueError, StopIteration):
            await replace_message(
                message,
                repository,
                "Введите число от 0,1 до 100.",
                reply_markup=back_keyboard(),
            )
            return
        except Exception:
            await replace_message(
                message,
                repository,
                "⚠️ Walt временно недоступен. Повторите позже.",
                reply_markup=back_keyboard(),
            )
            return
        alert_id = await repository.add_alert(
            kind=AlertKind.PERCENT,
            asset=asset,
            quote_side=side,
            target=str(percent),
            baseline=str(baseline),
        )
        await state.clear()
        await replace_message(
            message,
            repository,
            f"✅ Процентное уведомление A{alert_id} создано.",
            reply_markup=main_menu_keyboard(),
        )

    @router.message(Command("schedule"))
    async def schedule_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(ScheduleFlow.kind)
            await replace_message(
                message, repository, "Выберите тип сводки:", reply_markup=schedule_keyboard()
            )

    @router.callback_query(ScheduleFlow.kind, F.data.startswith("schedule:kind:"))
    async def schedule_kind(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        kind = callback.data.rsplit(":", 1)[1]
        await state.update_data(kind=kind)
        await state.set_state(ScheduleFlow.value)
        prompt = (
            "Введите интервал в часах от 1 до 168:"
            if kind == "interval"
            else "Введите время ЧЧ:ММ (Екатеринбург):"
        )
        await _callback_message(callback, prompt, back_keyboard())

    @router.message(ScheduleFlow.value)
    async def schedule_value(message: Message, state: FSMContext) -> None:
        if not await owner_required(message, repository):
            return
        data = await state.get_data()
        try:
            if data["kind"] == "interval":
                hours = int(message.text or "")
                if not 1 <= hours <= 168:
                    raise ValueError
                schedule_id = await repository.add_schedule(
                    kind=ScheduleKind.INTERVAL, interval_hours=hours
                )
            else:
                daily_time = parse_daily_time(message.text or "")
                schedule_id = await repository.add_schedule(
                    kind=ScheduleKind.DAILY, daily_time=daily_time
                )
        except ValueError:
            await replace_message(
                message,
                repository,
                "Некорректное значение. Проверьте формат и диапазон.",
                reply_markup=back_keyboard(),
            )
            return
        await state.clear()
        await replace_message(
            message,
            repository,
            f"✅ Расписание S{schedule_id} создано.",
            reply_markup=main_menu_keyboard(),
        )

    @router.message(Command("delete"))
    async def delete_command(message: Message) -> None:
        if not await owner_required(message, repository):
            return
        alerts = await repository.list_alerts()
        schedules = await repository.list_schedules()
        if not alerts and not schedules:
            await replace_message(message, repository, "Удалять нечего — активных правил нет.")
            return
        await replace_message(
            message,
            repository,
            "Выберите правило:",
            reply_markup=delete_keyboard(alerts, schedules),
        )

    @router.callback_query(F.data.startswith("delete:"))
    async def delete_callback(callback: CallbackQuery) -> None:
        if not await owner_required(callback, repository):
            return
        _, kind, identifier = callback.data.split(":", 2)
        deleted = (
            await repository.delete_alert(int(identifier))
            if kind == "alert"
            else await repository.delete_schedule(int(identifier))
        )
        await _callback_message(
            callback,
            "✅ Правило удалено." if deleted else "Правило уже удалено.",
            main_menu_keyboard(),
        )

    @router.callback_query(F.data == "menu:price")
    async def menu_price(callback: CallbackQuery) -> None:
        if not await owner_required(callback, repository):
            return
        try:
            text = format_market_summary(await market_provider.get_snapshots())
        except Exception:
            text = "⚠️ Не удалось получить цены Walt. Повторите немного позже."
        await _callback_message(callback, text, main_menu_keyboard())

    @router.callback_query(F.data == "menu:home")
    async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.clear()
            await _callback_message(callback, WELCOME, main_menu_keyboard())

    @router.callback_query(F.data == "menu:alert")
    async def menu_alert(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(AlertFlow.asset)
            await _callback_message(callback, "Выберите монету:", asset_keyboard("alert"))

    @router.callback_query(F.data == "menu:change")
    async def menu_change(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(ChangeFlow.asset)
            await _callback_message(callback, "Выберите монету:", asset_keyboard("change"))

    @router.callback_query(F.data == "menu:schedule")
    async def menu_schedule(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(ScheduleFlow.kind)
            await _callback_message(callback, "Выберите тип сводки:", schedule_keyboard())

    @router.callback_query(F.data == "menu:alerts")
    async def menu_alerts(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await _callback_message(
                callback,
                format_rules(await repository.list_alerts(), await repository.list_schedules()),
                main_menu_keyboard(),
            )

    @router.callback_query(F.data == "menu:delete")
    async def menu_delete(callback: CallbackQuery) -> None:
        if not await owner_required(callback, repository):
            return
        alerts = await repository.list_alerts()
        schedules = await repository.list_schedules()
        if not alerts and not schedules:
            await _callback_message(
                callback, "Удалять нечего — активных правил нет.", main_menu_keyboard()
            )
            return
        await _callback_message(callback, "Выберите правило:", delete_keyboard(alerts, schedules))

    @router.callback_query(F.data == "menu:pause")
    async def menu_pause(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await repository.set_paused(True)
            await _callback_message(callback, "⏸ Уведомления приостановлены.", main_menu_keyboard())

    @router.callback_query(F.data == "menu:resume")
    async def menu_resume(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await repository.set_paused(False)
            await _callback_message(callback, "▶️ Уведомления возобновлены.", main_menu_keyboard())

    @router.callback_query(F.data == "menu:help")
    async def menu_help(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await _callback_message(callback, HELP, main_menu_keyboard())

    return router


async def _callback_message(
    callback: CallbackQuery, text: str, reply_markup: Any | None = None
) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=reply_markup)
