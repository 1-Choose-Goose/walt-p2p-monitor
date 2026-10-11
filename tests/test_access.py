from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from walt_price_bot.access import AccessMiddleware, AccessRequirement
from walt_price_bot.models import AccessStatus
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, Repository


class FakeMessage:
    def __init__(self, telegram_id: int, *, text: str | None = None) -> None:
        self.from_user = SimpleNamespace(id=telegram_id)
        self.text = text
        self.contact = None
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class FakeCallback:
    def __init__(self, telegram_id: int) -> None:
        self.from_user = SimpleNamespace(id=telegram_id)
        self.message = object()
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str, *, show_alert: bool) -> None:
        self.answers.append((text, show_alert))


def handler_data(requirement: AccessRequirement) -> dict[str, object]:
    return {"handler": SimpleNamespace(flags={"access": requirement})}


@pytest.mark.asyncio
async def test_public_handler_allows_unknown_user_and_attaches_none(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
    handler = AsyncMock(return_value="ok")
    data = handler_data(AccessRequirement.PUBLIC)

    result = await middleware(handler, FakeMessage(101), data)

    assert result == "ok"
    assert data["event_user"] is None


@pytest.mark.asyncio
async def test_start_is_public_when_dispatcher_does_not_expose_handler_flags(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
    handler = AsyncMock(return_value="registration")
    event = FakeMessage(101, text="/start")

    result = await middleware(
        handler,
        event,
        {"handler": SimpleNamespace(flags={})},
    )

    assert result == "registration"
    handler.assert_awaited_once()
    assert event.answers == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_text"),
    [
        (None, "Сначала отправьте заявку на доступ."),
        (AccessStatus.PENDING, "Заявка уже отправлена и ожидает решения."),
        (AccessStatus.AWAITING_TOKEN, "Доступ одобрен. Добавьте токен Walt."),
        (AccessStatus.REJECTED, "Заявка отклонена. Можно отправить новую."),
        (AccessStatus.REVOKED, "Доступ к боту отозван."),
    ],
)
async def test_active_handler_denies_non_active_statuses(
    tmp_path: Path, status: AccessStatus | None, expected_text: str
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    if status is not None:
        await repository.create_or_refresh_request(
            101, phone="+79990000001", first_name="Иван", username=None
        )
        if status is not AccessStatus.PENDING:
            await repository.transition_user(
                101, expected=AccessStatus.PENDING, new_status=status
            )
    middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
    event = FakeMessage(101)
    handler = AsyncMock()

    result = await middleware(
        handler, event, handler_data(AccessRequirement.ACTIVE)
    )

    assert result is None
    assert event.answers == [expected_text]
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_denial_uses_alert_and_never_calls_handler(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
    callback = FakeCallback(101)
    handler = AsyncMock()

    await middleware(handler, callback, handler_data(AccessRequirement.ACTIVE))

    assert callback.answers == [("Сначала отправьте заявку на доступ.", True)]
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_requirement_uses_fixed_administrator(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    middleware = AccessMiddleware(repository, ADMIN_TELEGRAM_ID)
    handler = AsyncMock(return_value="admin")
    data = handler_data(AccessRequirement.ADMIN)

    result = await middleware(handler, FakeMessage(ADMIN_TELEGRAM_ID), data)

    assert result == "admin"
    assert data["event_user"].telegram_id == ADMIN_TELEGRAM_ID


def test_middleware_rejects_configurable_admin_override(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")

    with pytest.raises(ValueError, match="administrator"):
        AccessMiddleware(repository, 123)

