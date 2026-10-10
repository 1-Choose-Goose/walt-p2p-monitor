from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from walt_price_bot.messaging import (
    DeleteIncomingMessagesMiddleware,
    ReplacingSender,
    replace_message,
)
from walt_price_bot.repository import Repository


@pytest.mark.asyncio
async def test_incoming_user_message_is_deleted_after_handling() -> None:
    event = SimpleNamespace(delete=AsyncMock())
    handler = AsyncMock(return_value="готово")

    result = await DeleteIncomingMessagesMiddleware()(handler, event, {})

    assert result == "готово"
    event.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_replacing_sender_deletes_previous_bot_message(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(10)
    bot = SimpleNamespace(
        delete_message=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)),
    )

    await ReplacingSender(bot, repository, reply_markup="кнопки").send_message(101, "Новая цена")

    bot.delete_message.assert_awaited_once_with(101, 10)
    bot.send_message.assert_awaited_once_with(101, "Новая цена", reply_markup="кнопки")
    assert await repository.get_last_message_id() == 11


@pytest.mark.asyncio
async def test_interactive_reply_replaces_previous_bot_message(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(20)
    bot = SimpleNamespace(delete_message=AsyncMock())
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=101),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=21)),
    )

    await replace_message(message, repository, "Актуальное сообщение")

    bot.delete_message.assert_awaited_once_with(101, 20)
    assert await repository.get_last_message_id() == 21
