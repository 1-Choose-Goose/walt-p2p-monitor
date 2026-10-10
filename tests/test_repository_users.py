from decimal import Decimal
from pathlib import Path

import pytest

from walt_price_bot.models import AccessStatus, Asset, UserRole
from walt_price_bot.repository import (
    ADMIN_TELEGRAM_ID,
    DuplicatePhoneError,
    InvalidUserTransitionError,
    Repository,
)


@pytest.mark.asyncio
async def test_admin_is_created_and_cannot_be_changed_or_deleted(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    admin = await repository.get_user(ADMIN_TELEGRAM_ID)
    assert admin is not None
    assert admin.role is UserRole.ADMIN
    assert admin.status is AccessStatus.ACTIVE
    assert await repository.delete_user(ADMIN_TELEGRAM_ID) is False
    with pytest.raises(InvalidUserTransitionError):
        await repository.set_user_role(ADMIN_TELEGRAM_ID, UserRole.USER)


@pytest.mark.asyncio
async def test_request_is_idempotent_but_phone_is_unique(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    first = await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван", username="ivan"
    )
    repeated = await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван 2", username="ivan2"
    )

    assert first.telegram_id == repeated.telegram_id == 101
    assert repeated.first_name == "Иван 2"
    assert repeated.status is AccessStatus.PENDING
    with pytest.raises(DuplicatePhoneError):
        await repository.create_or_refresh_request(
            202, phone="+79990000001", first_name="Пётр", username=None
        )


@pytest.mark.asyncio
async def test_user_state_transitions_are_atomic(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван", username=None
    )

    approved = await repository.transition_user(
        101,
        expected=AccessStatus.PENDING,
        new_status=AccessStatus.AWAITING_TOKEN,
    )
    assert approved.status is AccessStatus.AWAITING_TOKEN

    with pytest.raises(InvalidUserTransitionError):
        await repository.transition_user(
            101,
            expected=AccessStatus.PENDING,
            new_status=AccessStatus.ACTIVE,
        )


@pytest.mark.asyncio
async def test_user_lists_are_filtered_and_paginated(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for telegram_id in (101, 102, 103):
        await repository.create_or_refresh_request(
            telegram_id,
            phone=f"+79990000{telegram_id}",
            first_name=str(telegram_id),
            username=None,
        )

    page = await repository.list_users(status=AccessStatus.PENDING, limit=2, offset=1)
    assert [user.telegram_id for user in page] == [102, 103]


@pytest.mark.asyncio
async def test_preferences_assets_sessions_and_credentials_are_isolated(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for telegram_id in (101, 202):
        await repository.create_or_refresh_request(
            telegram_id,
            phone=f"+79990000{telegram_id}",
            first_name=str(telegram_id),
            username=None,
        )

    await repository.update_preferences(101, min_limit_from=Decimal("1000"))
    await repository.replace_user_assets(101, (Asset.USDT, Asset.ETH))
    await repository.save_ui_session(101, "settings:limits", {"draft": "1500"})
    await repository.set_encrypted_credential(101, b"ciphertext")

    assert (await repository.get_preferences(101)).min_limit_from == Decimal("1000")
    assert (await repository.get_preferences(202)).min_limit_from == Decimal("500")
    assert await repository.get_user_assets(101) == (Asset.USDT, Asset.ETH)
    assert len(await repository.get_user_assets(202)) == 5
    assert await repository.load_ui_session(101) == (
        "settings:limits",
        {"draft": "1500"},
    )
    assert await repository.get_encrypted_credential(101) == b"ciphertext"
    assert await repository.get_encrypted_credential(202) is None


@pytest.mark.asyncio
async def test_delete_user_cascades_and_stale_lookup_is_safe(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.create_or_refresh_request(
        101, phone="+79990000001", first_name="Иван", username=None
    )
    await repository.set_encrypted_credential(101, b"ciphertext")
    await repository.save_ui_session(101, "settings", {"page": 2})

    assert await repository.delete_user(101) is True
    assert await repository.delete_user(101) is False
    assert await repository.get_user(101) is None
    assert await repository.get_encrypted_credential(101) is None
    assert await repository.load_ui_session(101) is None


@pytest.mark.asyncio
async def test_catalog_can_add_disable_and_retain_future_asset(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    await repository.upsert_asset(
        symbol=Asset("XRP"),
        walt_code="XRP",
        exchange_code="XRP",
        active=True,
        sort_order=10,
    )
    assert [entry.symbol for entry in await repository.list_assets()][-1] == Asset("XRP")

    await repository.set_asset_active(Asset("XRP"), False)
    assert Asset("XRP") not in [entry.symbol for entry in await repository.list_assets()]
    assert Asset("XRP") in [
        entry.symbol for entry in await repository.list_assets(active_only=False)
    ]

