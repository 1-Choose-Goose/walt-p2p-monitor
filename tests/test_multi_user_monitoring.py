import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from walt_price_bot.credentials import CredentialCipher
from walt_price_bot.models import AccessStatus, Asset, MarketSnapshot, QuietHours
from walt_price_bot.monitoring import (
    UserMarketProviderFactory,
    in_quiet_hours,
    interval_summary_is_due,
    run_multi_user_cycle,
    run_user_cycle,
)
from walt_price_bot.repository import Repository
from walt_price_bot.runtime import run_monitor_cycle


def test_quiet_hours_support_midnight_and_normal_ranges() -> None:
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 30), QuietHours(True, "22:00", "08:00"))
    assert in_quiet_hours(datetime(2026, 1, 2, 7, 59), QuietHours(True, "22:00", "08:00"))
    assert not in_quiet_hours(datetime(2026, 1, 2, 12, 0), QuietHours(True, "22:00", "08:00"))
    assert in_quiet_hours(datetime(2026, 1, 2, 13, 0), QuietHours(True, "12:00", "14:00"))
    assert in_quiet_hours(datetime(2026, 1, 2, 23, 0), QuietHours(True, "23:00", "07:00"))
    assert in_quiet_hours(datetime(2026, 1, 3, 6, 59), QuietHours(True, "23:00", "07:00"))
    assert not in_quiet_hours(datetime(2026, 1, 3, 7, 0), QuietHours(True, "23:00", "07:00"))
    assert not in_quiet_hours(datetime(2026, 1, 2, 14, 0), QuietHours(True, "12:00", "14:00"))


def test_one_minute_summary_is_due_and_restart_safe() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert interval_summary_is_due(None, now, 1)
    assert not interval_summary_is_due((now - timedelta(seconds=59)).isoformat(), now, 1)
    assert interval_summary_is_due((now - timedelta(minutes=1)).isoformat(), now, 1)


@pytest.mark.asyncio
async def test_summary_never_includes_unselected_provider_assets(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.replace_user_assets(768929900, (Asset.GRAM, Asset.USDT))
    await repository.update_preferences(
        768929900, summary_enabled=True, summary_interval_minutes=1
    )
    provider = AsyncMock()
    provider.get_snapshots.return_value = [
        MarketSnapshot(asset=asset, buy=None, sell=None)
        for asset in (Asset.GRAM, Asset.USDT, Asset.BTC, Asset.NOT, Asset.ETH)
    ]
    sender = AsyncMock()

    await run_monitor_cycle(
        repository,
        provider,
        sender,
        datetime.now(UTC),
        telegram_id=768929900,
    )

    text = sender.send_message.await_args.args[1]
    assert "GRAM / RUB" in text
    assert "USDT / RUB" in text
    assert "BTC / RUB" not in text
    assert "NOT / RUB" not in text
    assert "ETH / RUB" not in text


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
async def test_hundred_users_never_exceed_monitoring_concurrency_limit(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for telegram_id in range(1000, 1099):
        await repository.create_or_refresh_request(
            telegram_id,
            phone=f"+799900{telegram_id:05d}",
            first_name=None,
            username=None,
        )
        await repository.transition_user(
            telegram_id,
            expected=AccessStatus.PENDING,
            new_status=AccessStatus.ACTIVE,
        )
    factory = AsyncMock()
    factory.for_user.return_value = AsyncMock()
    active = 0
    peak = 0
    processed = 0

    async def runner(*_: object) -> None:
        nonlocal active, peak, processed
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        processed += 1
        active -= 1

    await run_multi_user_cycle(
        repository,
        factory,
        AsyncMock(),
        datetime.now(UTC),
        concurrency=5,
        run_user=runner,
    )

    assert processed == 100
    assert peak <= 5


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


@pytest.mark.asyncio
async def test_user_monitor_interval_prevents_early_market_request(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.update_preferences(768929900, monitor_interval_seconds=60)
    user = await repository.get_user(768929900)
    provider = AsyncMock()
    provider.get_snapshots.return_value = []
    sender = AsyncMock()
    now = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)
    await repository.mark_monitor_cycle(
        user.telegram_id, (now - timedelta(seconds=30)).isoformat()
    )

    await run_user_cycle(user, repository, provider, sender, now)
    provider.get_snapshots.assert_not_awaited()

    await run_user_cycle(user, repository, provider, sender, now + timedelta(seconds=31))
    provider.get_snapshots.assert_awaited_once()


@pytest.mark.asyncio
async def test_quiet_hours_use_user_timezone_and_resume_with_current_market(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.update_preferences(
        768929900,
        quiet_enabled=True,
        quiet_start="23:00",
        quiet_end="07:00",
        timezone="Asia/Yekaterinburg",
    )
    user = await repository.get_user(768929900)
    provider = AsyncMock()
    provider.get_snapshots.return_value = []
    sender = AsyncMock()

    await run_user_cycle(
        user,
        repository,
        provider,
        sender,
        datetime(2026, 10, 10, 20, 30, tzinfo=UTC),
    )
    provider.get_snapshots.assert_not_awaited()
    assert (await repository.get_delivery_times(user.telegram_id))[0] is None

    await run_user_cycle(
        user,
        repository,
        provider,
        sender,
        datetime(2026, 10, 11, 2, 0, tzinfo=UTC),
    )
    provider.get_snapshots.assert_awaited_once()


@pytest.mark.asyncio
async def test_opportunity_and_message_delivery_use_distinct_locks(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    assert repository.opportunity_lock(768929900) is not repository.user_lock(768929900)
