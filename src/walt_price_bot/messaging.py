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
    def __init__(
        self,
        bot: Any,
        repository: Repository,
        *,
        reply_markup: Any = None,
        reply_markup_factory: Callable[[int], Awaitable[Any]] | None = None,
        edit_existing: bool = False,
    ) -> None:
        self._bot = bot
        self._repository = repository
        self._reply_markup = reply_markup
        self._reply_markup_factory = reply_markup_factory
        self._edit_existing = edit_existing

    async def send_message(self, chat_id: int, text: str) -> Any:
        async with self._repository.user_lock(chat_id):
            previous_id = await self._repository.get_last_message_id(chat_id)
            reply_markup = (
                await self._reply_markup_factory(chat_id)
                if self._reply_markup_factory is not None
                else self._reply_markup
            )
            if previous_id is not None and self._edit_existing:
                try:
                    sent = await self._bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=previous_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                except TelegramAPIError:
                    pass
                else:
                    await self._repository.set_last_message_id(chat_id, previous_id)
                    return sent
            if previous_id is not None:
                with contextlib.suppress(TelegramAPIError):
                    await self._bot.delete_message(chat_id, previous_id)
            sent = await self._bot.send_message(chat_id, text, reply_markup=reply_markup)
            await self._repository.set_last_message_id(chat_id, sent.message_id)
            return sent
