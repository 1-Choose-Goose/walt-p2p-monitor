import asyncio
import contextlib
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand

from .config import Settings
from .handlers import build_router
from .messaging import ReplacingSender
from .repository import Repository
from .runtime import MarketService, monitor_forever
from .ui import main_menu_keyboard
from .walt import WaltClient

logger = logging.getLogger(__name__)


async def set_commands_best_effort(bot: Bot) -> None:
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="price", description="Текущие цены"),
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
        dispatcher = Dispatcher()

        async with aiohttp.ClientSession() as session:
            client = WaltClient(
                session=session,
                api_key=settings.walt_api_key.get_secret_value(),
            )
            market = MarketService(client)
            dispatcher.include_router(build_router(repository, market))
            await set_commands_best_effort(bot)
            monitor_task = asyncio.create_task(
                monitor_forever(
                    repository,
                    market,
                    ReplacingSender(bot, repository, reply_markup=main_menu_keyboard()),
                    interval_seconds=settings.poll_interval_seconds,
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
