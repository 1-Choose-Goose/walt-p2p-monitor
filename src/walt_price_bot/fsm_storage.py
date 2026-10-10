from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from .repository import Repository


class SQLiteFSMStorage(BaseStorage):
    """Persistent aiogram state backed by the bot's tenant database."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @staticmethod
    def _telegram_id(key: StorageKey) -> int:
        if key.user_id is None:
            raise ValueError("FSM storage requires a Telegram user")
        return key.user_id

    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        telegram_id = self._telegram_id(key)
        current = await self.repository.load_ui_session(telegram_id)
        data = current[1] if current else {}
        state_value = state.state if isinstance(state, State) else state
        await self.repository.save_ui_session(telegram_id, state_value or "", data)

    async def get_state(self, key: StorageKey) -> str | None:
        current = await self.repository.load_ui_session(self._telegram_id(key))
        return current[0] or None if current else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        telegram_id = self._telegram_id(key)
        current = await self.repository.load_ui_session(telegram_id)
        state = current[0] if current else ""
        await self.repository.save_ui_session(telegram_id, state, data)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        current = await self.repository.load_ui_session(self._telegram_id(key))
        return dict(current[1]) if current else {}

    async def update_data(
        self, key: StorageKey, data: dict[str, Any]
    ) -> dict[str, Any]:
        telegram_id = self._telegram_id(key)
        async with self.repository.user_lock(telegram_id):
            current = await self.repository.load_ui_session(telegram_id)
            state, stored = current if current else ("", {})
            updated = {**stored, **data}
            await self.repository.save_ui_session(telegram_id, state, updated)
        return updated

    async def close(self) -> None:
        return None
