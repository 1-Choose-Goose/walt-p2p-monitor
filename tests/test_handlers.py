from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from walt_price_bot.handlers import _callback_message, handle_start, owner_required
from walt_price_bot.repository import Repository


@pytest.mark.asyncio
async def test_first_start_claims_owner_and_greets(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    answer = AsyncMock(return_value=SimpleNamespace(message_id=77))
    message = SimpleNamespace(from_user=SimpleNamespace(id=101), answer=answer)

    await handle_start(message, repository)

    assert await repository.get_owner_id() == 101
    assert "Walt P2P" in message.answer.await_args.args[0]
    assert await repository.get_last_message_id() == 77


@pytest.mark.asyncio
async def test_other_user_is_denied_without_leaking_data(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.claim_owner(101)
    message = SimpleNamespace(from_user=SimpleNamespace(id=202), answer=AsyncMock())

    allowed = await owner_required(message, repository)

    assert allowed is False
    assert message.answer.await_args.args == ("⛔ Этот бот доступен только владельцу.",)


@pytest.mark.asyncio
async def test_repeated_callback_ignores_message_not_modified(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    callback = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=object(), message="Bad Request: message is not modified"
                )
            ),
            answer=AsyncMock(),
        ),
    )

    await _callback_message(callback, repository, "Меню")

    callback.answer.assert_awaited_once()
    callback.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_callback_restores_current_message(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    callback = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=object(), message="Bad Request: message to edit not found"
                )
            ),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        ),
    )

    await _callback_message(callback, repository, "Меню")

    callback.message.answer.assert_awaited_once()
    assert await repository.get_last_message_id() == 99
