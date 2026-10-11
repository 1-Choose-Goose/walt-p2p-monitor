import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardMarkup

import walt_price_bot.main as bot_main
from walt_price_bot.main import default_bot_properties
from walt_price_bot.messaging import (
    DeleteIncomingMessagesMiddleware,
    ReplacingSender,
    replace_message,
)
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, Repository


@pytest.mark.parametrize(
    "module_name",
    ["onboarding.py", "preferences.py", "admin.py"],
)
def test_user_responses_use_single_message_replacement(module_name: str) -> None:
    source_path = Path(__file__).parents[1] / "src" / "walt_price_bot" / module_name
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    direct_answers = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "answer"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "message"
    ]

    assert direct_answers == []


@pytest.mark.asyncio
async def test_incoming_user_message_is_deleted_after_handling() -> None:
    event = SimpleNamespace(delete=AsyncMock())
    handler = AsyncMock(return_value="готово")

    result = await DeleteIncomingMessagesMiddleware()(handler, event, {})

    assert result == "готово"
    event.delete.assert_awaited_once()


def test_telegram_link_previews_are_disabled() -> None:
    properties = default_bot_properties()

    assert properties.link_preview_is_disabled is True


@pytest.mark.asyncio
async def test_background_notifications_replace_summary_with_notifying_message_and_menu(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 30)
    bot = SimpleNamespace(
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(return_value=SimpleNamespace(message_id=30)),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=31)),
    )
    factory = getattr(bot_main, "build_message_sender", None)

    assert callable(factory)
    await factory(bot, repository).send_message(ADMIN_TELEGRAM_ID, "Выгодный лот")

    bot.edit_message_text.assert_not_awaited()
    bot.delete_message.assert_awaited_once_with(ADMIN_TELEGRAM_ID, 30)
    bot.send_message.assert_awaited_once()
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert any(button.text == "👥 Пользователи" for row in markup.inline_keyboard for button in row)
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 31


@pytest.mark.asyncio
async def test_replacing_sender_deletes_previous_bot_message(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 10)
    bot = SimpleNamespace(
        delete_message=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)),
    )

    await ReplacingSender(bot, repository, reply_markup="кнопки").send_message(
        ADMIN_TELEGRAM_ID, "Новая цена"
    )

    bot.delete_message.assert_awaited_once_with(ADMIN_TELEGRAM_ID, 10)
    bot.send_message.assert_awaited_once_with(
        ADMIN_TELEGRAM_ID, "Новая цена", reply_markup="кнопки"
    )
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 11


@pytest.mark.asyncio
async def test_interactive_reply_replaces_previous_bot_message(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 20)
    bot = SimpleNamespace(delete_message=AsyncMock())
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        from_user=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=21)),
    )

    await replace_message(message, repository, "Актуальное сообщение")

    bot.delete_message.assert_awaited_once_with(ADMIN_TELEGRAM_ID, 20)
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 21


@pytest.mark.asyncio
async def test_concurrent_replacements_are_serialized(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 10)

    class Bot:
        def __init__(self) -> None:
            self.deleted: list[int] = []
            self.next_id = 11

        async def delete_message(self, chat_id: int, message_id: int) -> None:
            assert chat_id == ADMIN_TELEGRAM_ID
            self.deleted.append(message_id)

        async def send_message(self, chat_id: int, text: str, **_: object) -> object:
            assert chat_id == ADMIN_TELEGRAM_ID
            await asyncio.sleep(0)
            message_id = self.next_id
            self.next_id += 1
            return SimpleNamespace(message_id=message_id)

    bot = Bot()
    sender = ReplacingSender(bot, repository)

    await asyncio.gather(
        sender.send_message(ADMIN_TELEGRAM_ID, "Первое"),
        sender.send_message(ADMIN_TELEGRAM_ID, "Второе"),
    )

    assert bot.deleted == [10, 11]
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 12


@pytest.mark.asyncio
async def test_interactive_and_background_replacements_share_lock(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 10)
    deleted: list[int] = []

    async def delete_message(_: int, message_id: int) -> None:
        deleted.append(message_id)

    bot = SimpleNamespace(
        delete_message=delete_message,
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=12)),
    )
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        from_user=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=11)),
    )

    await asyncio.gather(
        replace_message(message, repository, "Кнопка"),
        ReplacingSender(bot, repository).send_message(ADMIN_TELEGRAM_ID, "Уведомление"),
    )

    assert deleted == [10, 11]
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 12
