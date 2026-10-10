from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from walt_price_bot.credentials import CredentialCipher
from walt_price_bot.models import AccessStatus, QuietHours
from walt_price_bot.monitoring import (
    UserMarketProviderFactory,
    in_quiet_hours,
    interval_summary_is_due,
    run_multi_user_cycle,
    run_user_cycle,
)
from walt_price_bot.repository import Repository


def test_quiet_hours_support_midnight_and_normal_ranges() -> None:
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 30), QuietHours(True, "22:00", "08:00"))
    assert in_quiet_hours(datetime(2026, 1, 2, 7, 59), QuietHours(True, "22:00", "08:00"))
    assert not in_quiet_hours(datetime(2026, 1, 2, 12, 0), QuietHours(True, "22:00", "08:00"))
    assert in_quiet_hours(datetime(2026, 1, 2, 13, 0), QuietHours(True, "12:00", "14:00"))


def test_one_minute_summary_is_due_and_restart_safe() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert interval_summary_is_due(None, now, 1)
    assert not interval_summary_is_due((now - timedelta(seconds=59)).isoformat(), now, 1)
    assert interval_summary_is_due((now - timedelta(minutes=1)).isoformat(), now, 1)


@pytest.mark.asyncio
async def test_factory_uses_distinct_decrypted_tokens(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))
    users = []
    for telegram_id, token in ((101, "token-one"), (202, "token-two")):
        await repository.create_or_refresh_request(
            telegram_id,
            phone=f"+79990000{telegram_id:03d}",
            first_name=None,
            username=None,
        )
        await repository.transition_user(
            telegram_id, expected=AccessStatus.PENDING, new_status=AccessStatus.ACTIVE
        )
        await repository.set_encrypted_credential(telegram_id, cipher.encrypt(token))
        users.append(await repository.get_user(telegram_id))
    factory = UserMarketProviderFactory(object(), repository, cipher, object())

    first = await factory.for_user(users[0])
    second = await factory.for_user(users[1])

    assert first._client._api_key == "token-one"
    assert second._client._api_key == "token-two"


@pytest.mark.asyncio
async def test_multi_user_cycle_isolates_one_user_failure(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for telegram_id in (101, 202):
        await repository.create_or_refresh_request(
            telegram_id,
            phone=f"+79990000{telegram_id:03d}",
            first_name=None,
            username=None,
        )
        await repository.transition_user(
            telegram_id, expected=AccessStatus.PENDING, new_status=AccessStatus.ACTIVE
        )
    factory = AsyncMock()
    factory.for_user.side_effect = [RuntimeError("bad token"), AsyncMock()]
    runner = AsyncMock()

    await run_multi_user_cycle(
        repository,
        factory,
        AsyncMock(),
        datetime.now(UTC),
        run_user=runner,
    )

    assert runner.await_count == 1
    assert runner.await_args.args[0].telegram_id == 202


@pytest.mark.asyncio
async def test_one_minute_summary_is_marked_only_after_send(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.update_preferences(
        768929900, summary_enabled=True, summary_interval_minutes=1
    )
    user = await repository.get_user(768929900)
    provider = AsyncMock()
    provider.get_snapshots.return_value = []
    sender = AsyncMock()
    sender.send_message.side_effect = RuntimeError("telegram unavailable")
    now = datetime.now(UTC)

    await run_user_cycle(user, repository, provider, sender, now)
    assert (await repository.get_delivery_times(user.telegram_id))[1] is None

    await repository.mark_monitor_cycle(
        user.telegram_id, (now - timedelta(minutes=1)).isoformat()
    )
    sender.send_message.side_effect = None
    await run_user_cycle(user, repository, provider, sender, now)
    assert (await repository.get_delivery_times(user.telegram_id))[1] == now.isoformat()
