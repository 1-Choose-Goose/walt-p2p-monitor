import asyncio
import contextlib
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand

from .access import AccessMiddleware
from .admin import build_admin_router
from .config import Settings
from .credentials import CredentialCipher
from .exchange import CoinbaseRatesClient
from .fsm_storage import SQLiteFSMStorage
from .handlers import build_router
from .messaging import ReplacingSender
from .models import SEED_ASSETS
from .monitoring import UserMarketProviderFactory, monitor_multi_user_forever
from .onboarding import build_onboarding_router, migrate_legacy_admin_token
from .preferences import build_preferences_router
from .repository import ADMIN_TELEGRAM_ID, Repository

logger = logging.getLogger(__name__)

BOT_SHORT_DESCRIPTION = "Лучшие P2P-цены Walt в рублях."
BOT_DESCRIPTION = (
    "Следит за P2P-предложениями Walt, сравнивает их с биржевым курсом и сообщает "
    "о выгодных ценах. Монеты, суммы, способы оплаты и уведомления настраиваются "
    "индивидуально.\n\n/start — открыть главное меню."
)


def default_bot_properties() -> DefaultBotProperties:
    return DefaultBotProperties(
        parse_mode=ParseMode.HTML,
        link_preview_is_disabled=True,
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
                BotCommand(command="token", description="Токен Walt"),
                BotCommand(command="settings", description="Настройки"),
                BotCommand(command="admin", description="Пользователи"),
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
    cipher = CredentialCipher(settings.credential_encryption_key)
    await migrate_legacy_admin_token(repository, cipher, settings.walt_api_key)
    if settings.owner_telegram_id is not None:
        await repository.set_configured_owner(settings.owner_telegram_id)

    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=default_bot_properties(),
    )
    try:
        dispatcher = Dispatcher(storage=SQLiteFSMStorage(repository))
        access_middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
        dispatcher.message.outer_middleware(access_middleware)
        dispatcher.callback_query.outer_middleware(access_middleware)

        async with aiohttp.ClientSession() as session:
            factory = UserMarketProviderFactory(
                session,
                repository,
                cipher,
                CoinbaseRatesClient(session=session),
            )
            dispatcher.include_router(
                build_onboarding_router(
                    repository,
                    cipher,
                    session,
                    ADMIN_TELEGRAM_ID,
                    SEED_ASSETS[1],
                )
            )
            dispatcher.include_router(build_admin_router(repository, ADMIN_TELEGRAM_ID))
            dispatcher.include_router(build_preferences_router(repository))
            dispatcher.include_router(build_router(repository, factory))
            await set_commands_best_effort(bot)
            await set_profile_best_effort(bot)
            monitor_task = asyncio.create_task(
                monitor_multi_user_forever(
                    repository,
                    factory,
                    ReplacingSender(bot, repository),
                    tick_seconds=min(settings.poll_interval_seconds, 15),
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
