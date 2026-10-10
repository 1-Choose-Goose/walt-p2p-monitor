import asyncio
import contextlib
import logging
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand

from .access import AccessMiddleware
from .config import Settings
from .exchange import CoinbaseRatesClient
from .fsm_storage import SQLiteFSMStorage
from .handlers import build_router
from .messaging import ReplacingSender
from .repository import ADMIN_TELEGRAM_ID, Repository
from .runtime import MarketService, monitor_forever
from .ui import main_menu_keyboard
from .walt import WaltClient

logger = logging.getLogger(__name__)

BOT_SHORT_DESCRIPTION = "Мониторинг P2P-цен Walt в рублях."
BOT_DESCRIPTION = (
    "Бот мониторит подходящие P2P-предложения Walt для USDT, TON и BTC в рублях, "
    "показывает биржевой курс Coinbase и уведомляет о расчётно выгодных лотах. "
    "Нажмите /start, чтобы начать работу."
)


async def set_commands_best_effort(bot: Bot) -> None:
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="price", description="Текущие цены"),
                BotCommand(command="deals", description="Выгодные лоты"),
                BotCommand(command="alert", description="Ценовой порог"),
                BotCommand(command="change", description="Изменение в процентах"),
                BotCommand(command="schedule", description="Регулярная сводка"),
                BotCommand(command="alerts", description="Активные правила"),
                BotCommand(command="delete", description="Удалить правило"),
                BotCommand(command="pause", description="Пауза уведомлений"),
                BotCommand(command="resume", description="Возобновить уведомления"),
                BotCommand(command="help", description="Справка"),
            ]
        )
    except TelegramNetworkError:
        logger.warning("Telegram command menu registration postponed after network error")


async def set_profile_best_effort(bot: Bot) -> None:
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        await bot.set_my_description(description=BOT_DESCRIPTION)
    except TelegramNetworkError:
        logger.warning("Telegram bot profile update postponed after network error")


async def start_polling_resilient(
    dispatcher: Dispatcher,
    bot: Bot,
    *,
    allowed_updates: list[str],
    retry_delay: float = 10,
) -> None:
    while True:
        try:
            await dispatcher.start_polling(bot, allowed_updates=allowed_updates)
            return
        except TelegramNetworkError:
            logger.warning("Telegram polling connection failed; retrying")
            await asyncio.sleep(retry_delay)


async def run() -> None:
    settings = Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    repository = Repository(settings.database_path)
    await repository.initialize()
    if settings.owner_telegram_id is not None:
        await repository.set_configured_owner(settings.owner_telegram_id)

    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        dispatcher = Dispatcher(storage=SQLiteFSMStorage(repository))
        access_middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
        dispatcher.message.outer_middleware(access_middleware)
        dispatcher.callback_query.outer_middleware(access_middleware)

        async with aiohttp.ClientSession() as session:
            client = WaltClient(
                session=session,
                api_key=settings.walt_api_key.get_secret_value(),
            )
            market = MarketService(client, CoinbaseRatesClient(session=session))
            dispatcher.include_router(build_router(repository, market))
            await set_commands_best_effort(bot)
            await set_profile_best_effort(bot)
            monitor_task = asyncio.create_task(
                monitor_forever(
                    repository,
                    market,
                    ReplacingSender(bot, repository, reply_markup=main_menu_keyboard()),
                    interval_seconds=settings.poll_interval_seconds,
                    schedule_timezone=ZoneInfo(settings.timezone),
                ),
                name="walt-monitor",
            )
            try:
                await start_polling_resilient(
                    dispatcher,
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                )
            finally:
                monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor_task
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
