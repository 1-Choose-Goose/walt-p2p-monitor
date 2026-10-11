from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from walt_price_bot.admin import (
    AdminAction,
    AdminCallback,
    AdminPageCallback,
    _list_keyboard,
    _user_keyboard,
    apply_admin_action,
    format_user_list,
    load_admin_page,
)
from walt_price_bot.models import AccessStatus, UserRole
from walt_price_bot.repository import (
    ADMIN_TELEGRAM_ID,
    InvalidUserTransitionError,
    Repository,
)


async def requested_user(repository: Repository, telegram_id: int = 101):
    return await repository.create_or_refresh_request(
        telegram_id,
        phone=f"+79990000{telegram_id:03d}",
        first_name="Иван",
        username="ivan",
    )


@pytest.mark.asyncio
async def test_user_list_is_compact_and_paginated(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for telegram_id in (101, 102, 103):
        await requested_user(repository, telegram_id)

    page = await repository.list_users(limit=2, offset=1)
    text = format_user_list(page, page=2)

    assert "Страница 2" in text
    assert "102" in text and "101" not in text
    assert page[0].version == 0


def test_admin_callback_round_trip_contains_stale_guard() -> None:
    packed = AdminCallback(
        action=AdminAction.APPROVE,
        target_id=101,
        expected_status=AccessStatus.PENDING,
        expected_version=3,
        page=2,
    ).pack()
    unpacked = AdminCallback.unpack(packed)
    assert unpacked.target_id == 101
    assert unpacked.expected_version == 3
    assert unpacked.page == 2


@pytest.mark.asyncio
async def test_approve_commits_then_notifies_user(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    user = await requested_user(repository)
    notify = AsyncMock()

    updated = await apply_admin_action(
        repository,
        ADMIN_TELEGRAM_ID,
        AdminAction.APPROVE,
        user.telegram_id,
        user.status,
        user.version,
        notify=notify,
    )

    assert updated.status is AccessStatus.AWAITING_TOKEN
    assert (await repository.get_user(101)).status is AccessStatus.AWAITING_TOKEN
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_failure_does_not_rollback_decision(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    user = await requested_user(repository)
    notify = AsyncMock(side_effect=RuntimeError("blocked"))

    updated = await apply_admin_action(
        repository,
        ADMIN_TELEGRAM_ID,
        AdminAction.REJECT,
        user.telegram_id,
        user.status,
        user.version,
        notify=notify,
    )

    assert updated.status is AccessStatus.REJECTED
    assert (await repository.get_user(101)).status is AccessStatus.REJECTED


@pytest.mark.asyncio
async def test_roles_revoke_restore_and_stale_callback(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    user = await requested_user(repository)
    user = await repository.transition_user(
        101, expected=AccessStatus.PENDING, new_status=AccessStatus.AWAITING_TOKEN
    )
    user = await repository.transition_user(
        101, expected=AccessStatus.AWAITING_TOKEN, new_status=AccessStatus.ACTIVE
    )
    user = await apply_admin_action(
        repository, ADMIN_TELEGRAM_ID, AdminAction.PREMIUM, 101, user.status, user.version
    )
    assert user.role is UserRole.PREMIUM
    revoked = await apply_admin_action(
        repository, ADMIN_TELEGRAM_ID, AdminAction.REVOKE, 101, user.status, user.version
    )
    restored = await apply_admin_action(
        repository,
        ADMIN_TELEGRAM_ID,
        AdminAction.RESTORE,
        101,
        revoked.status,
        revoked.version,
    )
    assert restored.status is AccessStatus.AWAITING_TOKEN
    with pytest.raises(InvalidUserTransitionError, match="changed"):
        await apply_admin_action(
            repository, ADMIN_TELEGRAM_ID, AdminAction.USER, 101, restored.status, 0
        )


@pytest.mark.asyncio
async def test_non_admin_and_fixed_admin_are_immutable(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    with pytest.raises(PermissionError):
        await apply_admin_action(
            repository,
            101,
            AdminAction.REVOKE,
            ADMIN_TELEGRAM_ID,
            AccessStatus.ACTIVE,
            0,
        )
    with pytest.raises(InvalidUserTransitionError, match="administrator"):
        await apply_admin_action(
            repository,
            ADMIN_TELEGRAM_ID,
            AdminAction.REVOKE,
            ADMIN_TELEGRAM_ID,
            AccessStatus.ACTIVE,
            0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("user_count", [1, 19, 20, 21, 40, 100])
async def test_admin_pages_cover_every_non_admin_user_once(
    tmp_path: Path, user_count: int
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for index in range(user_count):
        await requested_user(repository, 1000 + index)

    seen: list[int] = []
    page = 0
    while True:
        users, page, has_previous, has_next = await load_admin_page(
            repository, page, ADMIN_TELEGRAM_ID
        )
        seen.extend(user.telegram_id for user in users)
        assert has_previous is (page > 0)
        if not has_next:
            break
        page += 1

    assert len(seen) == user_count
    assert len(set(seen)) == user_count
    assert ADMIN_TELEGRAM_ID not in seen


@pytest.mark.asyncio
async def test_admin_card_keeps_second_page_navigation(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    for index in range(21):
        await requested_user(repository, 2000 + index)
    users, page, has_previous, has_next = await load_admin_page(
        repository, 1, ADMIN_TELEGRAM_ID
    )

    list_keyboard = _list_keyboard(users, page, has_previous, has_next)
    open_callback = AdminCallback.unpack(list_keyboard.inline_keyboard[0][0].callback_data)
    card_keyboard = _user_keyboard(users[0], open_callback.page)
    back_callback = AdminPageCallback.unpack(
        card_keyboard.inline_keyboard[-1][0].callback_data
    )

    assert open_callback.page == 1
    assert back_callback.page == 1

    updated = await apply_admin_action(
        repository,
        ADMIN_TELEGRAM_ID,
        AdminAction.APPROVE,
        open_callback.target_id,
        open_callback.expected_status,
        open_callback.expected_version,
    )

    assert updated.status is AccessStatus.AWAITING_TOKEN
    stored = await repository.get_user(open_callback.target_id)
    assert stored.status is AccessStatus.AWAITING_TOKEN
