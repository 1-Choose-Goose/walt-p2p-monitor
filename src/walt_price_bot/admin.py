import contextlib
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .access import AccessRequirement
from .models import AccessStatus, User, UserRole
from .repository import ADMIN_TELEGRAM_ID, InvalidUserTransitionError, Repository


class AdminAction(StrEnum):
    OPEN = "open"
    APPROVE = "approve"
    REJECT = "reject"
    REVOKE = "revoke"
    RESTORE = "restore"
    USER = "user"
    PREMIUM = "premium"
    DELETE = "delete"
    CONFIRM_DELETE = "confirm_delete"


class AdminCallback(CallbackData, prefix="admin"):
    action: AdminAction
    target_id: int
    expected_status: AccessStatus
    expected_version: int
    page: int = 0


def format_user_list(users: Sequence[User], *, page: int) -> str:
    lines = [f"👥 <b>Пользователи · Страница {page}</b>"]
    for user in users:
        name = user.first_name or "Без имени"
        lines.append(
            f"\n{name} · <code>{user.telegram_id}</code>\n"
            f"{user.status.value} · {user.role.value} · {user.phone or 'без номера'}"
        )
    if not users:
        lines.append("\nСписок пуст.")
    return "\n".join(lines)


def _user_keyboard(user: User, page: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    actions: list[tuple[AdminAction, str]] = []
    if user.status is AccessStatus.PENDING:
        actions.extend(
            [(AdminAction.APPROVE, "✅ Одобрить"), (AdminAction.REJECT, "❌ Отклонить")]
        )
    elif user.status is AccessStatus.ACTIVE:
        actions.append((AdminAction.REVOKE, "🚫 Отозвать доступ"))
    elif user.status in {AccessStatus.REVOKED, AccessStatus.REJECTED}:
        actions.append((AdminAction.RESTORE, "♻️ Восстановить"))
    if user.role is not UserRole.ADMIN:
        actions.append(
            (AdminAction.USER if user.role is UserRole.PREMIUM else AdminAction.PREMIUM,
             "Роль: USER" if user.role is UserRole.PREMIUM else "Роль: PREMIUM")
        )
        actions.append((AdminAction.DELETE, "🗑 Удалить"))
    for action, text in actions:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=AdminCallback(
                        action=action,
                        target_id=user.telegram_id,
                        expected_status=user.status,
                        expected_version=user.version,
                        page=page,
                    ).pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _list_keyboard(users: Sequence[User], page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{user.first_name or 'Без имени'} · {user.telegram_id}",
                    callback_data=AdminCallback(
                        action=AdminAction.OPEN,
                        target_id=user.telegram_id,
                        expected_status=user.status,
                        expected_version=user.version,
                        page=page,
                    ).pack(),
                )
            ]
            for user in users
        ]
    )


def _delete_confirmation(user: User, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить пользователя",
                    callback_data=AdminCallback(
                        action=AdminAction.CONFIRM_DELETE,
                        target_id=user.telegram_id,
                        expected_status=user.status,
                        expected_version=user.version,
                        page=page,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=AdminCallback(
                        action=AdminAction.OPEN,
                        target_id=user.telegram_id,
                        expected_status=user.status,
                        expected_version=user.version,
                        page=page,
                    ).pack(),
                )
            ],
        ]
    )


async def apply_admin_action(
    repository: Repository,
    actor_id: int,
    action: AdminAction,
    target_id: int,
    expected_status: AccessStatus,
    expected_version: int,
    *,
    notify: Callable[[int, str], Awaitable[Any]] | None = None,
) -> User:
    if actor_id != ADMIN_TELEGRAM_ID:
        raise PermissionError("administrator access required")
    if target_id == ADMIN_TELEGRAM_ID:
        raise InvalidUserTransitionError("administrator is immutable")

    notification: str | None = None
    if action in {AdminAction.USER, AdminAction.PREMIUM}:
        role = UserRole.USER if action is AdminAction.USER else UserRole.PREMIUM
        updated = await repository.set_user_role(
            target_id, role, expected_version=expected_version
        )
    elif action is AdminAction.CONFIRM_DELETE:
        current = await repository.get_user(target_id)
        if (
            current is None
            or current.status is not expected_status
            or current.version != expected_version
        ):
            raise InvalidUserTransitionError("user state has changed")
        await repository.delete_user(target_id)
        return current
    else:
        transitions = {
            AdminAction.APPROVE: AccessStatus.AWAITING_TOKEN,
            AdminAction.REJECT: AccessStatus.REJECTED,
            AdminAction.REVOKE: AccessStatus.REVOKED,
        }
        if action is AdminAction.RESTORE:
            has_token = await repository.get_encrypted_credential(target_id) is not None
            new_status = AccessStatus.ACTIVE if has_token else AccessStatus.AWAITING_TOKEN
        else:
            new_status = transitions.get(action)
        if new_status is None:
            raise ValueError("unsupported administrator action")
        updated = await repository.transition_user(
            target_id,
            expected=expected_status,
            new_status=new_status,
            expected_version=expected_version,
        )
        notification = {
            AdminAction.APPROVE: "✅ Доступ одобрен. Добавьте токен Walt командой /token.",
            AdminAction.REJECT: "Заявка на доступ отклонена.",
            AdminAction.REVOKE: "Доступ к боту отозван.",
            AdminAction.RESTORE: "✅ Доступ к боту восстановлен.",
        }.get(action)

    if notify is not None and notification is not None:
        with contextlib.suppress(Exception):
            await notify(target_id, notification)
    return updated


def build_admin_router(repository: Repository, admin_id: int) -> Router:
    if admin_id != ADMIN_TELEGRAM_ID:
        raise ValueError("administrator id cannot be overridden")
    router = Router(name="admin")

    @router.message(Command("admin"), flags={"access": AccessRequirement.ADMIN})
    async def admin_panel(message: Message) -> None:
        users = [
            user
            for user in await repository.list_users(limit=20)
            if user.telegram_id != admin_id
        ]
        await message.answer(
            format_user_list(users, page=1), reply_markup=_list_keyboard(users, 0)
        )

    @router.callback_query(F.data == "menu:admin", flags={"access": AccessRequirement.ADMIN})
    async def admin_panel_callback(callback: CallbackQuery) -> None:
        users = [
            user
            for user in await repository.list_users(limit=20)
            if user.telegram_id != admin_id
        ]
        if callback.message:
            await callback.message.edit_text(
                format_user_list(users, page=1),
                reply_markup=_list_keyboard(users, 0),
            )
        await callback.answer()

    @router.callback_query(AdminCallback.filter(), flags={"access": AccessRequirement.ADMIN})
    async def action(callback: CallbackQuery, callback_data: AdminCallback) -> None:
        current = await repository.get_user(callback_data.target_id)
        if current is None:
            await callback.answer("Пользователь уже удалён.", show_alert=True)
            return
        if callback_data.action is AdminAction.OPEN:
            if callback.message:
                await callback.message.edit_text(
                    format_user_list([current], page=callback_data.page + 1),
                    reply_markup=_user_keyboard(current, callback_data.page),
                )
            await callback.answer()
            return
        if callback_data.action is AdminAction.DELETE:
            if callback.message:
                await callback.message.edit_text(
                    "Удалить пользователя и все его настройки?",
                    reply_markup=_delete_confirmation(current, callback_data.page),
                )
            await callback.answer()
            return
        try:
            user = await apply_admin_action(
                repository,
                callback.from_user.id,
                callback_data.action,
                callback_data.target_id,
                callback_data.expected_status,
                callback_data.expected_version,
                notify=callback.bot.send_message,
            )
        except InvalidUserTransitionError:
            await callback.answer("Данные уже изменились. Обновите список.", show_alert=True)
            return
        await callback.answer("Готово")
        if callback.message:
            await callback.message.edit_text(
                format_user_list([user], page=callback_data.page + 1),
                reply_markup=_user_keyboard(user, callback_data.page),
            )

    return router
