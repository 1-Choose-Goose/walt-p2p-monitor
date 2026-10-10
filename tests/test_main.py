import logging

import pytest
from aiogram.exceptions import TelegramNetworkError

from walt_price_bot.main import (
    set_commands_best_effort,
    set_profile_best_effort,
    start_polling_resilient,
)


class NetworkFailingBot:
    async def set_my_commands(self, commands: object) -> None:
        raise TelegramNetworkError(method=object(), message="temporary timeout")


class RecordingBot:
    def __init__(self) -> None:
        self.commands: object = None

    async def set_my_commands(self, commands: object) -> None:
        self.commands = commands


class ProfileRecordingBot:
    def __init__(self) -> None:
        self.description: str | None = None
        self.short_description: str | None = None

    async def set_my_description(self, description: str) -> None:
        self.description = description

    async def set_my_short_description(self, short_description: str) -> None:
        self.short_description = short_description


class RecoveringDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    async def start_polling(self, bot: object, *, allowed_updates: list[str]) -> None:
        self.calls += 1
        if self.calls == 1:
            raise TelegramNetworkError(method=object(), message="temporary timeout")


@pytest.mark.asyncio
async def test_command_registration_network_failure_does_not_abort_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        await set_commands_best_effort(NetworkFailingBot())

    assert "command menu registration postponed" in caplog.text


@pytest.mark.asyncio
async def test_command_menu_includes_start_as_main_menu() -> None:
    bot = RecordingBot()

    await set_commands_best_effort(bot)

    assert bot.commands is not None
    assert (bot.commands[0].command, bot.commands[0].description) == ("start", "Главное меню")
    assert any(
        command.command == "deals" and command.description == "Выгодные лоты"
        for command in bot.commands
    )
    assert {command.command for command in bot.commands} == {
        "start",
        "price",
        "deals",
        "alert",
        "change",
        "schedule",
        "alerts",
        "delete",
        "pause",
        "resume",
        "help",
        "token",
        "settings",
        "admin",
    }


@pytest.mark.asyncio
async def test_bot_profile_has_russian_pre_start_greeting() -> None:
    bot = ProfileRecordingBot()

    await set_profile_best_effort(bot)

    assert bot.short_description == "Лучшие P2P-цены Walt в рублях."
    assert bot.description is not None
    assert "P2P-предложениями Walt" in bot.description
    assert "биржевым курсом" in bot.description
    assert "выгодных" in bot.description.lower()
    assert "/start" not in bot.description


@pytest.mark.asyncio
async def test_polling_retries_after_network_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = RecoveringDispatcher()

    with caplog.at_level(logging.WARNING):
        await start_polling_resilient(
            dispatcher,
            object(),
            allowed_updates=["message"],
            retry_delay=0,
        )

    assert dispatcher.calls == 2
    assert "Telegram polling connection failed; retrying" in caplog.text
