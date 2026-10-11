import contextlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Contact,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pydantic import SecretStr

from .access import AccessRequirement
from .credentials import CredentialCipher
from .keyboards import (
    main_navigation_keyboard,
    phone_request_keyboard,
    remove_reply_keyboard,
)
from .messaging import DeleteIncomingMessagesMiddleware, ReplacingSender, replace_message
from .models import AccessStatus, AssetCatalogEntry
from .repository import ADMIN_TELEGRAM_ID, Repository
from .ui import main_menu_keyboard
from .walt import TokenValidationResult, TokenValidationStatus, validate_walt_token

WELCOME = (
    "👋 <b>Walt P2P</b>\n\n"
    "Показываю лучшие предложения Walt в рублях и сообщаю, когда появляется "
    "подходящая цена.\n\n"
    "Настройте монеты, сумму и способы оплаты один раз — бот всё запомнит."
)


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    notify_admin: bool
    status: AccessStatus


def registration_feedback(result: RegistrationResult) -> str:
    if result.status is AccessStatus.ACTIVE:
        return "✅ Номер подтверждён и привязан к аккаунту."
    if result.status is AccessStatus.AWAITING_TOKEN:
        return "✅ Доступ уже одобрен. Теперь отправьте токен Walt командой /token."
    if result.status is AccessStatus.REVOKED:
        return "🚫 Доступ отозван. Восстановить его может только администратор."
    if result.notify_admin:
        return "✅ Номер подтверждён. Заявка отправлена администратору."
    return "Заявка уже отправлена и ожидает решения администратора."


class TokenFlow(StatesGroup):
    token = State()


def normalize_phone(contact: Contact, sender_id: int) -> str:
    if contact.user_id is None:
        raise ValueError("Отправьте контакт Telegram кнопкой ниже.")
    if contact.user_id != sender_id:
        raise ValueError("Можно отправить только свой контакт Telegram.")
    digits = re.sub(r"\D", "", contact.phone_number)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise ValueError("В контакте указан некорректный номер телефона.")
    return "+" + digits


async def register_contact(
    repository: Repository, contact: Contact, sender: Any
) -> RegistrationResult:
    phone = normalize_phone(contact, sender.id)
    previous = await repository.get_user(sender.id)
    account = await repository.create_or_refresh_request(
        sender.id,
        phone=phone,
        first_name=getattr(sender, "first_name", None),
        username=getattr(sender, "username", None),
    )
    return RegistrationResult(
        notify_admin=previous is None or previous.status is AccessStatus.REJECTED,
        status=account.status,
    )


async def approve_access(repository: Repository, telegram_id: int) -> None:
    await repository.transition_user(
        telegram_id,
        expected=AccessStatus.PENDING,
        new_status=AccessStatus.AWAITING_TOKEN,
    )


async def activate_user_token(
    repository: Repository,
    cipher: CredentialCipher,
    validator: Callable[
        [Any, SecretStr, AssetCatalogEntry], Awaitable[TokenValidationResult]
    ],
    telegram_id: int,
    token: SecretStr,
    probe_asset: AssetCatalogEntry,
    *,
    session: Any = None,
) -> TokenValidationResult:
    result = await validator(session, token, probe_asset)
    if result.status is not TokenValidationStatus.VALID:
        return result
    encrypted = cipher.encrypt(token.get_secret_value())
    user = await repository.get_user(telegram_id)
    if user is None:
        raise ValueError("user does not exist")
    await repository.set_encrypted_credential(telegram_id, encrypted)
    if user.status is AccessStatus.AWAITING_TOKEN:
        await repository.transition_user(
            telegram_id,
            expected=AccessStatus.AWAITING_TOKEN,
            new_status=AccessStatus.ACTIVE,
        )
    return result


async def process_token_message(
    message: Any,
    repository: Repository,
    cipher: CredentialCipher,
    validator: Callable[
        [Any, SecretStr, AssetCatalogEntry], Awaitable[TokenValidationResult]
    ],
    telegram_id: int,
    probe_asset: AssetCatalogEntry,
    *,
    session: Any = None,
) -> TokenValidationResult:
    try:
        result = await activate_user_token(
            repository,
            cipher,
            validator,
            telegram_id,
            SecretStr(message.text or ""),
            probe_asset,
            session=session,
        )
    finally:
        await message.delete()
    if result.status is TokenValidationStatus.VALID:
        user = await repository.get_user(telegram_id)
        if user is None:
            raise RuntimeError("activated user disappeared from the repository")
        await replace_message(
            message,
            repository,
            "✅ Токен Walt сохранён. Доступ к функциям открыт.",
            reply_markup=main_navigation_keyboard(user.role),
        )
    elif result.status is TokenValidationStatus.INVALID:
        await replace_message(
            message,
            repository,
            "❌ Токен Walt не принят. Проверьте его и попробуйте снова.",
        )
    else:
        await replace_message(
            message,
            repository,
            "⚠️ Walt временно недоступен. Токен не изменён, попробуйте позже.",
        )
    return result


async def migrate_legacy_admin_token(
    repository: Repository,
    cipher: CredentialCipher,
    legacy_token: SecretStr | None,
) -> bool:
    if legacy_token is None or await repository.get_encrypted_credential(ADMIN_TELEGRAM_ID):
        return False
    await repository.set_encrypted_credential(
        ADMIN_TELEGRAM_ID, cipher.encrypt(legacy_token.get_secret_value())
    )
    return True


def build_onboarding_router(
    repository: Repository,
    cipher: CredentialCipher,
    session: Any,
    admin_id: int,
    probe_asset: AssetCatalogEntry,
    *,
    active_home: Callable[[Message], Awaitable[None]] | None = None,
) -> Router:
    if admin_id != ADMIN_TELEGRAM_ID:
        raise ValueError("administrator id cannot be overridden")
    router = Router(name="onboarding")
    router.message.outer_middleware(DeleteIncomingMessagesMiddleware())

    @router.message(Command("start"), flags={"access": AccessRequirement.PUBLIC})
    async def start(message: Message, state: FSMContext) -> None:
        user = await repository.get_user(message.from_user.id)
        if user is not None:
            await state.clear()
        if user and user.status is AccessStatus.ACTIVE:
            if not user.phone:
                await replace_message(
                    message,
                    repository,
                    "Подтвердите номер телефона, чтобы завершить привязку аккаунта.",
                    reply_markup=phone_request_keyboard(),
                )
                return
            if active_home is not None:
                await active_home(message)
            else:
                await replace_message(
                    message,
                    repository,
                    WELCOME,
                    reply_markup=main_menu_keyboard(
                        user.role, await repository.is_paused(message.from_user.id)
                    ),
                )
            return
        if user and user.status is AccessStatus.PENDING:
            await replace_message(
                message, repository, "Заявка уже отправлена и ожидает решения."
            )
            return
        if user and user.status is AccessStatus.AWAITING_TOKEN:
            await state.set_state(TokenFlow.token)
            await replace_message(
                message, repository, "Отправьте токен Walt одним сообщением."
            )
            return
        await replace_message(
            message,
            repository,
            "Чтобы отправить заявку, подтвердите номер телефона.",
            reply_markup=phone_request_keyboard(),
        )

    @router.message(F.contact, flags={"access": AccessRequirement.PUBLIC})
    async def receive_contact(message: Message) -> None:
        try:
            result = await register_contact(
                repository, message.contact, message.from_user
            )
        except ValueError as exc:
            await replace_message(
                message,
                repository,
                str(exc),
                reply_markup=phone_request_keyboard(),
            )
            return
        account = await repository.get_user(message.from_user.id)
        reply_markup = (
            main_navigation_keyboard(account.role)
            if account and account.status is AccessStatus.ACTIVE
            else remove_reply_keyboard()
        )
        await replace_message(
            message,
            repository,
            registration_feedback(result),
            reply_markup=reply_markup,
        )
        if result.status is AccessStatus.ACTIVE and active_home is not None:
            await active_home(message)
        if result.notify_admin:
            username = (
                f"@{escape(message.from_user.username)}"
                if message.from_user.username
                else "без username"
            )
            await message.bot.send_message(
                admin_id,
                f"Новая заявка: {escape(message.from_user.full_name)} "
                f"({username}), ID {message.from_user.id}.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Одобрить",
                                callback_data=f"access:approve:{message.from_user.id}",
                            )
                        ]
                    ]
                ),
            )

    @router.callback_query(
        F.data.startswith("access:approve:"),
        flags={"access": AccessRequirement.PUBLIC},
    )
    async def approve(callback: CallbackQuery) -> None:
        if callback.from_user.id != admin_id:
            await callback.answer("Недостаточно прав.", show_alert=True)
            return
        target_id = int(callback.data.rsplit(":", 1)[1])
        await approve_access(repository, target_id)
        await ReplacingSender(callback.bot, repository).send_message(
            target_id, "✅ Доступ одобрен. Теперь отправьте токен Walt командой /token."
        )
        if callback.message:
            with contextlib.suppress(TelegramAPIError):
                await callback.message.delete()
        await callback.answer("Доступ одобрен")

    @router.message(Command("token"), flags={"access": AccessRequirement.PUBLIC})
    async def token_command(message: Message, state: FSMContext) -> None:
        user = await repository.get_user(message.from_user.id)
        if user is None or user.status not in {
            AccessStatus.AWAITING_TOKEN,
            AccessStatus.ACTIVE,
        }:
            await replace_message(
                message, repository, "Сначала получите доступ у администратора."
            )
            return
        await state.set_state(TokenFlow.token)
        await replace_message(
            message,
            repository,
            "Отправьте токен Walt одним сообщением. Сообщение будет удалено.",
        )

    @router.message(TokenFlow.token, F.text, flags={"access": AccessRequirement.PUBLIC})
    async def receive_token(message: Message, state: FSMContext) -> None:
        result = await process_token_message(
            message,
            repository,
            cipher,
            validate_walt_token,
            message.from_user.id,
            probe_asset,
            session=session,
        )
        if result.status is TokenValidationStatus.VALID:
            await state.clear()
            if active_home is not None:
                await active_home(message)

    return router
