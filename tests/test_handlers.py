from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from walt_price_bot.handlers import handle_start, owner_required
from walt_price_bot.repository import Repository


@pytest.mark.asyncio
async def test_first_start_claims_owner_and_greets(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    message = SimpleNamespace(from_user=SimpleNamespace(id=101), answer=AsyncMock())

    await handle_start(message, repository)

    assert await repository.get_owner_id() == 101
    assert "Walt P2P" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_other_user_is_denied_without_leaking_data(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.claim_owner(101)
    message = SimpleNamespace(from_user=SimpleNamespace(id=202), answer=AsyncMock())

    allowed = await owner_required(message, repository)

    assert allowed is False
    assert message.answer.await_args.args == ("⛔ Этот бот доступен только владельцу.",)
