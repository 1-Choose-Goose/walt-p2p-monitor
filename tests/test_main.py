import logging

import pytest
from aiogram.exceptions import TelegramNetworkError

from walt_price_bot.main import set_commands_best_effort


class NetworkFailingBot:
    async def set_my_commands(self, commands: object) -> None:
        raise TelegramNetworkError(method=object(), message="temporary timeout")


@pytest.mark.asyncio
async def test_command_registration_network_failure_does_not_abort_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        await set_commands_best_effort(NetworkFailingBot())

    assert "command menu registration postponed" in caplog.text
