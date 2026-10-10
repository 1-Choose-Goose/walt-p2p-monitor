from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from aiogram import BaseMiddleware

from .models import AccessStatus, UserRole
from .repository import ADMIN_TELEGRAM_ID, Repository


class AccessRequirement(StrEnum):
    PUBLIC = "PUBLIC"
    ACTIVE = "ACTIVE"
    ADMIN = "ADMIN"


_DENIAL_TEXT = {
    None: "Сначала отправьте заявку на доступ.",
    AccessStatus.PENDING: "Заявка уже отправлена и ожидает решения.",
    AccessStatus.AWAITING_TOKEN: "Доступ одобрен. Добавьте токен Walt.",
    AccessStatus.REJECTED: "Заявка отклонена. Можно отправить новую.",
    AccessStatus.REVOKED: "Доступ к боту отозван.",
}


class AccessMiddleware(BaseMiddleware):
    def __init__(self, repository: Repository, admin_id: int) -> None:
        if admin_id != ADMIN_TELEGRAM_ID:
            raise ValueError("administrator id cannot be overridden")
        self.repository = repository
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        sender = getattr(event, "from_user", None)
        telegram_id = getattr(sender, "id", None)
        user = await self.repository.get_user(telegram_id) if telegram_id else None
        data["event_user"] = user

        observer = data.get("handler")
        flags = getattr(observer, "flags", {})
        requirement = AccessRequirement(flags.get("access", AccessRequirement.ACTIVE))

        if requirement is AccessRequirement.PUBLIC:
            return await handler(event, data)

        allowed = bool(
            user
            and user.status is AccessStatus.ACTIVE
            and user.role in {UserRole.ADMIN, UserRole.USER, UserRole.PREMIUM}
        )
        if requirement is AccessRequirement.ADMIN:
            allowed = bool(allowed and telegram_id == self.admin_id and user.role is UserRole.ADMIN)

        if allowed:
            return await handler(event, data)

        text = _DENIAL_TEXT.get(
            user.status if user else None,
            "Эта функция доступна только администратору.",
        )
        if hasattr(event, "message"):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None
