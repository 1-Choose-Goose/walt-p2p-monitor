import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from .repository import Repository


class DeleteIncomingMessagesMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            with contextlib.suppress(TelegramAPIError):
                await event.delete()


async def replace_message(
    message: Message,
    repository: Repository,
    text: str,
    *,
    reply_markup: Any | None = None,
) -> Message:
    telegram_id = message.from_user.id
    async with repository.user_lock(telegram_id):
        previous_id = await repository.get_last_message_id(telegram_id)
        if previous_id is not None:
            with contextlib.suppress(TelegramAPIError):
                await message.bot.delete_message(message.chat.id, previous_id)
        sent = await message.answer(text, reply_markup=reply_markup)
        await repository.set_last_message_id(telegram_id, sent.message_id)
        return sent


class ReplacingSender:
    def __init__(self, bot: Any, repository: Repository, *, reply_markup: Any = None) -> None:
        self._bot = bot
        self._repository = repository
        self._reply_markup = reply_markup

    async def send_message(self, chat_id: int, text: str) -> Any:
        async with self._repository.user_lock(chat_id):
            previous_id = await self._repository.get_last_message_id(chat_id)
            if previous_id is not None:
                with contextlib.suppress(TelegramAPIError):
                    await self._bot.delete_message(chat_id, previous_id)
            sent = await self._bot.send_message(
                chat_id, text, reply_markup=self._reply_markup
            )
            await self._repository.set_last_message_id(chat_id, sent.message_id)
            return sent
