from pathlib import Path

import pytest
from aiogram.fsm.storage.base import StorageKey

from walt_price_bot.fsm_storage import SQLiteFSMStorage
from walt_price_bot.repository import Repository


@pytest.mark.asyncio
async def test_state_and_data_survive_storage_restart(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван", username=None
    )
    key = StorageKey(bot_id=1, chat_id=101, user_id=101)
    first = SQLiteFSMStorage(repository)

    await first.set_state(key, "settings:limits")
    await first.set_data(key, {"draft": "1500"})

    reopened = SQLiteFSMStorage(Repository(repository.path))
    assert await reopened.get_state(key) == "settings:limits"
    assert await reopened.get_data(key) == {"draft": "1500"}


@pytest.mark.asyncio
async def test_cancelling_state_does_not_erase_saved_data(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван", username=None
    )
    key = StorageKey(bot_id=1, chat_id=101, user_id=101)
    storage = SQLiteFSMStorage(repository)
    await storage.set_state(key, "settings:limits")
    await storage.set_data(key, {"previous": "1000"})

    await storage.set_state(key, None)

    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {"previous": "1000"}


@pytest.mark.asyncio
async def test_update_data_merges_values_atomically(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван", username=None
    )
    key = StorageKey(bot_id=1, chat_id=101, user_id=101)
    storage = SQLiteFSMStorage(repository)
    await storage.set_data(key, {"page": 1})

    updated = await storage.update_data(key, {"query": "BTC"})

    assert updated == {"page": 1, "query": "BTC"}
    assert await storage.get_data(key) == updated

