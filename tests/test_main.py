import logging

import pytest
from aiogram.exceptions import TelegramNetworkError

from walt_price_bot.main import set_commands_best_effort, start_polling_resilient


class NetworkFailingBot:
    async def set_my_commands(self, commands: object) -> None:
        raise TelegramNetworkError(method=object(), message="temporary timeout")


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
