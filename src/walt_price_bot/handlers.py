from decimal import Decimal
from typing import Any, Protocol

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .messaging import DeleteIncomingMessagesMiddleware, replace_message
from .models import (
    AccessStatus,
    AlertKind,
    Asset,
    MarketFilters,
    MarketSnapshot,
    OpportunityMode,
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
    format_opportunity_settings,
    format_rules,
    main_menu_keyboard,
    notifications_menu_keyboard,
    opportunity_mode_keyboard,
    opportunity_threshold_keyboard,
    parse_daily_time,
    parse_positive_decimal,
    schedule_keyboard,
    side_keyboard,
)

WELCOME = (
    "👋 <b>Walt P2P</b>\n\n"
    "Показываю лучшие предложения Walt в рублях и сообщаю, когда появляется "
    "подходящая цена.\n\n"
    "Настройте монеты, сумму и способы оплаты один раз — бот всё запомнит."
)

HELP = (
    "Выберите нужный раздел кнопками ниже. Настройки сохраняются автоматически."
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


class OpportunityFlow(StatesGroup):
    mode = State()
    threshold = State()
    custom_threshold = State()


async def owner_required(event: Message | CallbackQuery | Any, repository: Repository) -> bool:
    user = getattr(event, "from_user", None)
    account = await repository.get_user(user.id) if user is not None else None
    if account is not None and account.status is AccessStatus.ACTIVE:
        return True
    if isinstance(event, CallbackQuery):
        await event.answer("Доступ к этой функции пока не открыт.", show_alert=True)
    else:
        await event.answer("Доступ к этой функции пока не открыт.")
    return False


async def handle_start(message: Message | Any, repository: Repository) -> None:
    user = getattr(message, "from_user", None)
    account = await repository.get_user(user.id) if user is not None else None
    if account is None or account.status is not AccessStatus.ACTIVE:
        await message.answer("Доступ к этой функции пока не открыт.")
        return
    await replace_message(
        message,
        repository,
        WELCOME,
        reply_markup=main_menu_keyboard(
            account.role, await repository.is_paused(account.telegram_id)
        ),
    )


def build_router(repository: Repository, market_provider: MarketProvider) -> Router:
    router = Router(name=__name__)
    router.message.outer_middleware(DeleteIncomingMessagesMiddleware())

    async def update_callback(
        callback: CallbackQuery, text: str, reply_markup: Any | None = None
    ) -> None:
        await _callback_message(
            callback, repository, text, reply_markup=reply_markup
        )

    async def snapshots_for(telegram_id: int) -> list[MarketSnapshot]:
        account = await repository.get_user(telegram_id)
        provider = market_provider
        if hasattr(market_provider, "for_user"):
            provider = await market_provider.for_user(account)
        preferences = await repository.get_preferences(telegram_id)
        selected = set(preferences.assets)
        assets = [
            entry for entry in await repository.list_assets() if entry.symbol in selected
        ]
        filters = MarketFilters(
            directions=preferences.directions,
            min_limit_from=preferences.min_limit_from,
            min_limit_to=preferences.min_limit_to,
            desired_amount=preferences.desired_amount,
            buy_payments=preferences.buy_payments,
            sell_payments=preferences.sell_payments,
        )
        try:
            return await provider.get_snapshots(assets, filters)
        except TypeError:
            return await provider.get_snapshots()

    async def user_asset_keyboard(prefix: str, telegram_id: int):
        selected = set(await repository.get_user_assets(telegram_id))
        entries = [
            entry for entry in await repository.list_assets() if entry.symbol in selected
        ]
        return asset_keyboard(prefix, entries)

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
            snapshots = await snapshots_for(message.from_user.id)
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
                format_rules(
                    await repository.list_alerts(message.from_user.id),
                    await repository.list_schedules(message.from_user.id),
                    await repository.get_opportunity_settings(message.from_user.id),
                ),
                reply_markup=main_menu_keyboard(),
            )

    @router.message(Command("deals"))
    async def deals_command(message: Message, state: FSMContext) -> None:
        if await owner_required(message, repository):
            await state.set_state(OpportunityFlow.mode)
            await replace_message(
                message,
                repository,
                format_opportunity_settings(
                    await repository.get_opportunity_settings(message.from_user.id)
                ),
                reply_markup=opportunity_mode_keyboard(),
            )

    @router.message(Command("pause"))
    async def pause_command(message: Message) -> None:
        if await owner_required(message, repository):
            await repository.set_paused(message.from_user.id, True)
            await replace_message(
                message,
                repository,
                "⏸ Уведомления приостановлены.",
                reply_markup=main_menu_keyboard(),
            )

    @router.message(Command("resume"))
    async def resume_command(message: Message) -> None:
        if await owner_required(message, repository):
            await repository.set_paused(message.from_user.id, False)
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
                message,
                repository,
                "Выберите монету:",
                reply_markup=await user_asset_keyboard("alert", message.from_user.id),
            )

    @router.callback_query(AlertFlow.asset, F.data.startswith("alert:asset:"))
    async def alert_asset(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        asset = Asset(callback.data.rsplit(":", 1)[1])
        if asset not in await repository.get_user_assets(callback.from_user.id):
            await callback.answer("Эта монета не выбрана.", show_alert=True)
            return
        await state.update_data(asset=asset.value)
        await state.set_state(AlertFlow.side)
        await update_callback(callback, "Какую цену отслеживать?", side_keyboard("alert"))

    @router.callback_query(AlertFlow.side, F.data.startswith("alert:side:"))
    async def alert_side(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(side=callback.data.rsplit(":", 1)[1])
        await state.set_state(AlertFlow.direction)
        await update_callback(callback, "Когда уведомить?", direction_keyboard())

    @router.callback_query(AlertFlow.direction, F.data.startswith("alert:direction:"))
    async def alert_direction(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(direction=callback.data.rsplit(":", 1)[1])
        await state.set_state(AlertFlow.target)
        await update_callback(
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
            message.from_user.id,
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
                message,
                repository,
                "Выберите монету:",
                reply_markup=await user_asset_keyboard("change", message.from_user.id),
            )

    @router.callback_query(ChangeFlow.asset, F.data.startswith("change:asset:"))
    async def change_asset(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        asset = Asset(callback.data.rsplit(":", 1)[1])
        if asset not in await repository.get_user_assets(callback.from_user.id):
            await callback.answer("Эта монета не выбрана.", show_alert=True)
            return
        await state.update_data(asset=asset.value)
        await state.set_state(ChangeFlow.side)
        await update_callback(callback, "Какую цену отслеживать?", side_keyboard("change"))

    @router.callback_query(ChangeFlow.side, F.data.startswith("change:side:"))
    async def change_side(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await state.update_data(side=callback.data.rsplit(":", 1)[1])
        await state.set_state(ChangeFlow.percent)
        await update_callback(
            callback, "Введите процент изменения от 0,1 до 100:", back_keyboard()
        )

    @router.message(ChangeFlow.percent)
    async def change_percent(message: Message, state: FSMContext) -> None:
        if not await owner_required(message, repository):
            return
        try:
            percent = parse_positive_decimal(
                message.text or "",
                minimum=Decimal("0.1"),
                maximum=Decimal("100"),
            )
            data = await state.get_data()
            asset = Asset(data["asset"])
            side = QuoteSide(data["side"])
            snapshot = next(
                item
                for item in await snapshots_for(message.from_user.id)
                if item.asset == asset
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
            message.from_user.id,
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
        await update_callback(callback, prompt, back_keyboard())

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
                    message.from_user.id,
                    kind=ScheduleKind.INTERVAL, interval_hours=hours
                )
            else:
                daily_time = parse_daily_time(message.text or "")
                schedule_id = await repository.add_schedule(
                    message.from_user.id,
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
        alerts = await repository.list_alerts(message.from_user.id)
        schedules = await repository.list_schedules(message.from_user.id)
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
            await repository.delete_alert(callback.from_user.id, int(identifier))
            if kind == "alert"
            else await repository.delete_schedule(callback.from_user.id, int(identifier))
        )
        await update_callback(
            callback,
            "✅ Правило удалено." if deleted else "Правило уже удалено.",
            main_menu_keyboard(),
        )

    @router.callback_query(F.data == "menu:price")
    async def menu_price(callback: CallbackQuery) -> None:
        if not await owner_required(callback, repository):
            return
        try:
            text = format_market_summary(await snapshots_for(callback.from_user.id))
        except Exception:
            text = "⚠️ Не удалось получить цены Walt. Повторите немного позже."
        await update_callback(callback, text, main_menu_keyboard())

    @router.callback_query(F.data == "menu:home")
    async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.clear()
            await update_callback(callback, WELCOME, main_menu_keyboard())

    @router.callback_query(F.data == "menu:notifications")
    async def menu_notifications(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await update_callback(
                callback,
                "🔔 <b>Уведомления</b>\n\nВыберите, что настроить.",
                notifications_menu_keyboard(
                    await repository.is_paused(callback.from_user.id)
                ),
            )

    @router.callback_query(F.data == "menu:opportunity")
    async def menu_opportunity(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(OpportunityFlow.mode)
            await update_callback(
                callback,
                format_opportunity_settings(
                    await repository.get_opportunity_settings(callback.from_user.id)
                ),
                opportunity_mode_keyboard(),
            )

    @router.callback_query(
        OpportunityFlow.mode, F.data.startswith("opportunity:mode:")
    )
    async def opportunity_mode(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        mode = OpportunityMode(callback.data.rsplit(":", 1)[1])
        await state.update_data(opportunity_mode=mode.value)
        await state.set_state(OpportunityFlow.threshold)
        await update_callback(
            callback,
            "Выберите минимальную расчётную разницу с биржевым курсом:",
            opportunity_threshold_keyboard(),
        )

    @router.callback_query(
        OpportunityFlow.threshold, F.data.startswith("opportunity:threshold:")
    )
    async def opportunity_threshold(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        raw_value = callback.data.rsplit(":", 1)[1]
        if raw_value == "custom":
            await state.set_state(OpportunityFlow.custom_threshold)
            await update_callback(
                callback,
                "Введите минимальную наценку в процентах, например 1,5:",
                back_keyboard(),
            )
            return
        value = Decimal(raw_value)
        data = await state.get_data()
        await repository.configure_opportunities(
            callback.from_user.id,
            mode=OpportunityMode(data["opportunity_mode"]),
            min_spread_percent=value,
        )
        await state.clear()
        await update_callback(
            callback,
            "✅ Автоуведомления настроены.\n\n"
            + format_opportunity_settings(
                await repository.get_opportunity_settings(callback.from_user.id)
            ),
            main_menu_keyboard(),
        )

    @router.message(OpportunityFlow.custom_threshold)
    async def opportunity_custom_threshold(message: Message, state: FSMContext) -> None:
        try:
            value = parse_positive_decimal(
                message.text or "", minimum=Decimal("0.1"), maximum=Decimal("100")
            )
        except ValueError:
            await replace_message(
                message,
                repository,
                "Введите наценку от 0,1 до 100%.",
                reply_markup=back_keyboard(),
            )
            return
        data = await state.get_data()
        await repository.configure_opportunities(
            message.from_user.id,
            mode=OpportunityMode(data["opportunity_mode"]),
            min_spread_percent=value,
        )
        await state.clear()
        await replace_message(
            message,
            repository,
            "✅ Наценка сохранена.\n\n"
            + format_opportunity_settings(
                await repository.get_opportunity_settings(message.from_user.id)
            ),
            reply_markup=main_menu_keyboard(),
        )

    @router.callback_query(F.data == "opportunity:disable")
    async def opportunity_disable(callback: CallbackQuery, state: FSMContext) -> None:
        if not await owner_required(callback, repository):
            return
        await repository.disable_opportunities(callback.from_user.id)
        await state.clear()
        await update_callback(
            callback,
            "⛔ Автоуведомления о выгодных лотах отключены.",
            main_menu_keyboard(),
        )

    @router.callback_query(F.data == "menu:alert")
    async def menu_alert(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(AlertFlow.asset)
            await update_callback(
                callback,
                "Выберите монету:",
                await user_asset_keyboard("alert", callback.from_user.id),
            )

    @router.callback_query(F.data == "menu:change")
    async def menu_change(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(ChangeFlow.asset)
            await update_callback(
                callback,
                "Выберите монету:",
                await user_asset_keyboard("change", callback.from_user.id),
            )

    @router.callback_query(F.data == "menu:schedule")
    async def menu_schedule(callback: CallbackQuery, state: FSMContext) -> None:
        if await owner_required(callback, repository):
            await state.set_state(ScheduleFlow.kind)
            await update_callback(callback, "Выберите тип сводки:", schedule_keyboard())

    @router.callback_query(F.data == "menu:alerts")
    async def menu_alerts(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await update_callback(
                callback,
                format_rules(
                    await repository.list_alerts(callback.from_user.id),
                    await repository.list_schedules(callback.from_user.id),
                    await repository.get_opportunity_settings(callback.from_user.id),
                ),
                main_menu_keyboard(),
            )

    @router.callback_query(F.data == "menu:delete")
    async def menu_delete(callback: CallbackQuery) -> None:
        if not await owner_required(callback, repository):
            return
        alerts = await repository.list_alerts(callback.from_user.id)
        schedules = await repository.list_schedules(callback.from_user.id)
        if not alerts and not schedules:
            await update_callback(
                callback, "Удалять нечего — активных правил нет.", main_menu_keyboard()
            )
            return
        await update_callback(callback, "Выберите правило:", delete_keyboard(alerts, schedules))

    @router.callback_query(F.data == "menu:pause")
    async def menu_pause(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await repository.set_paused(callback.from_user.id, True)
            await update_callback(
                callback, "⏸ Уведомления приостановлены.", main_menu_keyboard()
            )

    @router.callback_query(F.data == "menu:resume")
    async def menu_resume(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await repository.set_paused(callback.from_user.id, False)
            await update_callback(
                callback, "▶️ Уведомления возобновлены.", main_menu_keyboard()
            )

    @router.callback_query(F.data == "menu:help")
    async def menu_help(callback: CallbackQuery) -> None:
        if await owner_required(callback, repository):
            await update_callback(callback, HELP, main_menu_keyboard())

    return router


async def _callback_message(
    callback: CallbackQuery,
    repository: Repository,
    text: str,
    reply_markup: Any | None = None,
) -> None:
    await callback.answer()
    if callback.message is not None:
        telegram_id = callback.from_user.id
        async with repository.user_lock(telegram_id):
            try:
                await callback.message.edit_text(text, reply_markup=reply_markup)
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
                sent = await callback.message.answer(text, reply_markup=reply_markup)
                await repository.set_last_message_id(telegram_id, sent.message_id)
