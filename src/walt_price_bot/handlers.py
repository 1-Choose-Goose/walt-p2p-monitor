from decimal import Decimal
from typing import Any, Protocol

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

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
    delete_keyboard,
    direction_keyboard,
    format_market_summary,
    format_rules,
    parse_daily_time,
    parse_positive_decimal,
    schedule_keyboard,
    side_keyboard,
)

WELCOME = (
    "👋 <b>Walt P2P Monitor</b>\n\n"
    "Слежу за лучшими ценами USDT/RUB и TON/RUB.\n"
    "Используйте /price, /alert, /change или /schedule."
)

HELP = (
    "<b>Команды</b>\n"
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
    await message.answer(WELCOME)


def build_router(repository: Repository, market_provider: MarketProvider) -> Router:
    router = Router(name=__name__)

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await handle_start(message, repository)

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if await owner_required(message, repository):
            await message.answer(HELP)

    @router.message(Command("price"))
    async def price_command(message: Message) -> None:
        if not await owner_required(message, repository):
            return
        try:
            snapshots = await market_provider.get_snapshots()
        except Exception:
            await message.answer("⚠️ Не удалось получить цены Walt. Повторите немного позже.")
            return
        await message.answer(format_market_summary(snapshots))

    @router.message(Command("alerts"))
    async def alerts_command(message: Message) -> None:
        if await owner_required(message, repository):
            await message.answer(
                format_rules(await repository.list_alerts(), await repository.list_schedules())
            )

    @router.message(Command("pause"))
    async def pause_command(message: Message) -> None:
        if await owner_required(message, repository):
            await repository.set_paused(True)
            await message.answer("⏸ Уведомления приостановлены.")

    @router.message(Command("resume"))
    async def resume_command(message: Message) -> None:
        if await owner_required(message, repository):
            await repository.set_paused(False)
            await message.answer("▶️ Уведомления возобновлены.")

    @router.message(Command("alert"))
    async def alert_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(AlertFlow.asset)
            await message.answer("Выберите монету:", reply_markup=asset_keyboard("alert"))

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
        await _callback_message(callback, "Введите цену в рублях, например 90,50:")

    @router.message(AlertFlow.target)
    async def alert_target(message: Message, state: FSMContext) -> None:
        if not await owner_required(message, repository):
            return
        try:
            target = parse_positive_decimal(message.text or "", maximum=Decimal("1000000000"))
        except ValueError:
            await message.answer("Введите корректную положительную цену в рублях.")
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
        await message.answer(f"✅ Ценовое уведомление A{alert_id} создано.")

    @router.message(Command("change"))
    async def change_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(ChangeFlow.asset)
            await message.answer("Выберите монету:", reply_markup=asset_keyboard("change"))

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
        await _callback_message(callback, "Введите процент изменения от 0,1 до 100:")

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
            baseline = snapshot.buy.price if side is QuoteSide.BUY else snapshot.sell.price
        except (ValueError, StopIteration):
            await message.answer("Введите число от 0,1 до 100.")
            return
        except Exception:
            await message.answer("⚠️ Walt временно недоступен. Повторите позже.")
            return
        alert_id = await repository.add_alert(
            kind=AlertKind.PERCENT,
            asset=asset,
            quote_side=side,
            target=str(percent),
            baseline=str(baseline),
        )
        await state.clear()
        await message.answer(f"✅ Процентное уведомление A{alert_id} создано.")

    @router.message(Command("schedule"))
    async def schedule_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(ScheduleFlow.kind)
            await message.answer("Выберите тип сводки:", reply_markup=schedule_keyboard())

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
        await _callback_message(callback, prompt)

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
            await message.answer("Некорректное значение. Проверьте формат и диапазон.")
            return
        await state.clear()
        await message.answer(f"✅ Расписание S{schedule_id} создано.")

    @router.message(Command("delete"))
    async def delete_command(message: Message) -> None:
        if not await owner_required(message, repository):
            return
        alerts = await repository.list_alerts()
        schedules = await repository.list_schedules()
        if not alerts and not schedules:
            await message.answer("Удалять нечего — активных правил нет.")
            return
        await message.answer("Выберите правило:", reply_markup=delete_keyboard(alerts, schedules))

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
            callback, "✅ Правило удалено." if deleted else "Правило уже удалено."
        )

    return router


async def _callback_message(
    callback: CallbackQuery, text: str, reply_markup: Any | None = None
) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(text, reply_markup=reply_markup)
