from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Contact, InlineKeyboardMarkup
from cryptography.fernet import Fernet
from pydantic import SecretStr

from walt_price_bot.credentials import CredentialCipher
from walt_price_bot.models import SEED_ASSETS, AccessStatus, User, UserRole
from walt_price_bot.onboarding import (
    activate_user_token,
    approve_access,
    build_onboarding_router,
    migrate_legacy_admin_token,
    normalize_phone,
    process_token_message,
    register_contact,
    registration_feedback,
)
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, DuplicatePhoneError, Repository
from walt_price_bot.walt import TokenValidationResult, TokenValidationStatus


def contact(phone: str = "8 (999) 000-00-01", user_id: int = 101) -> Contact:
    return Contact(phone_number=phone, first_name="Иван", user_id=user_id)


def test_phone_accepts_only_sender_native_contact_and_normalizes() -> None:
    assert normalize_phone(contact(), 101) == "+79990000001"
    with pytest.raises(ValueError, match="свой контакт"):
        normalize_phone(contact(user_id=202), 101)
    with pytest.raises(ValueError, match="контакт Telegram"):
        normalize_phone(Contact(phone_number="89990000001", first_name="Иван"), 101)


@pytest.mark.asyncio
async def test_registration_is_unique_and_notifies_admin_once(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    sender = SimpleNamespace(id=101, first_name="Иван", username="ivan")
    first = await register_contact(repository, contact(), sender)
    repeated = await register_contact(repository, contact(), sender)

    assert first.notify_admin is True
    assert repeated.notify_admin is False
    assert (await repository.get_user(101)).phone == "+79990000001"
    with pytest.raises(DuplicatePhoneError):
        await register_contact(
            repository,
            contact(user_id=202),
            SimpleNamespace(id=202, first_name="Пётр", username=None),
        )


@pytest.mark.asyncio
async def test_existing_admin_can_bind_native_phone(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    sender = SimpleNamespace(
        id=ADMIN_TELEGRAM_ID, first_name="Администратор", username=None
    )

    result = await register_contact(
        repository,
        contact(phone="8 (999) 000-00-99", user_id=ADMIN_TELEGRAM_ID),
        sender,
    )

    admin = await repository.get_user(ADMIN_TELEGRAM_ID)
    assert admin.phone == "+79990000099"
    assert admin.status is AccessStatus.ACTIVE
    assert result.notify_admin is False


@pytest.mark.asyncio
async def test_revoked_user_is_not_told_that_request_was_resubmitted(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    sender = SimpleNamespace(id=101, first_name="Иван", username="ivan")
    await register_contact(repository, contact(), sender)
    pending = await repository.get_user(101)
    approved = await repository.transition_user(
        101,
        expected=AccessStatus.PENDING,
        new_status=AccessStatus.AWAITING_TOKEN,
        expected_version=pending.version,
    )
    active = await repository.transition_user(
        101,
        expected=AccessStatus.AWAITING_TOKEN,
        new_status=AccessStatus.ACTIVE,
        expected_version=approved.version,
    )
    await repository.transition_user(
        101,
        expected=AccessStatus.ACTIVE,
        new_status=AccessStatus.REVOKED,
        expected_version=active.version,
    )

    result = await register_contact(repository, contact(), sender)

    assert result.status is AccessStatus.REVOKED
    assert result.notify_admin is False
    assert "отозван" in registration_feedback(result).lower()
    assert "отправлена" not in registration_feedback(result).lower()


@pytest.mark.asyncio
async def test_rejected_user_can_submit_a_fresh_request(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    sender = SimpleNamespace(id=101, first_name="Иван", username="ivan")
    await register_contact(repository, contact(), sender)
    pending = await repository.get_user(101)
    await repository.transition_user(
        101,
        expected=AccessStatus.PENDING,
        new_status=AccessStatus.REJECTED,
        expected_version=pending.version,
    )

    result = await register_contact(repository, contact(), sender)

    assert result.status is AccessStatus.PENDING
    assert result.notify_admin is True
    assert "отправлена" in registration_feedback(result).lower()


def test_admin_user_text_escapes_telegram_html() -> None:
    user = User(
        telegram_id=101,
        phone="+7<999>&\"",
        first_name="Иван <Admin> & Co",
        username="bad<name>",
        role=UserRole.USER,
        status=AccessStatus.PENDING,
        version=0,
    )

    from walt_price_bot.admin import format_user_list

    text = format_user_list([user], page=1)

    assert "Иван &lt;Admin&gt; &amp; Co" in text
    assert "+7&lt;999&gt;&amp;&quot;" in text
    assert "Иван <Admin>" not in text


@pytest.mark.asyncio
async def test_new_request_notification_escapes_telegram_profile(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    router = build_onboarding_router(
        repository,
        CredentialCipher(SecretStr(Fernet.generate_key().decode())),
        SimpleNamespace(),
        ADMIN_TELEGRAM_ID,
        SEED_ASSETS[1],
    )
    receive_contact = next(
        handler.callback
        for handler in router.message.handlers
        if handler.callback.__name__ == "receive_contact"
    )
    bot = SimpleNamespace(
        delete_message=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=88)),
    )
    sender = SimpleNamespace(
        id=101,
        first_name="Иван <Admin>",
        username="bad&name",
        full_name="Иван <Admin> & Co",
    )
    message = SimpleNamespace(
        contact=contact(),
        from_user=sender,
        chat=SimpleNamespace(id=101),
        bot=bot,
        answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
    )

    await receive_contact(message)

    admin_text = bot.send_message.await_args.args[1]
    assert "Иван &lt;Admin&gt; &amp; Co" in admin_text
    assert "@bad&amp;name" in admin_text
    assert "Иван <Admin>" not in admin_text


@pytest.mark.asyncio
async def test_active_start_replaces_previous_message_with_inline_menu(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    sender = SimpleNamespace(
        id=ADMIN_TELEGRAM_ID, first_name="Администратор", username=None
    )
    await register_contact(
        repository,
        contact(phone="8 (999) 000-00-99", user_id=ADMIN_TELEGRAM_ID),
        sender,
    )
    await repository.set_last_message_id(ADMIN_TELEGRAM_ID, 44)
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))
    router = build_onboarding_router(
        repository,
        cipher,
        SimpleNamespace(),
        ADMIN_TELEGRAM_ID,
        SEED_ASSETS[1],
    )
    start_handler = router.message.handlers[0].callback
    answer = AsyncMock(return_value=SimpleNamespace(message_id=77))
    delete_message = AsyncMock()
    message = SimpleNamespace(
        from_user=sender,
        chat=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        bot=SimpleNamespace(delete_message=delete_message),
        answer=answer,
    )

    await start_handler(message, AsyncMock())

    delete_message.assert_awaited_once_with(ADMIN_TELEGRAM_ID, 44)
    assert answer.await_count == 1
    assert isinstance(answer.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 77


@pytest.mark.asyncio
async def test_approval_then_successful_token_activation_is_encrypted(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await register_contact(
        repository,
        contact(),
        SimpleNamespace(id=101, first_name="Иван", username=None),
    )
    await approve_access(repository, 101)
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))
    validator = AsyncMock(return_value=TokenValidationResult(TokenValidationStatus.VALID))

    result = await activate_user_token(
        repository, cipher, validator, 101, SecretStr("private-walt-token"), SEED_ASSETS[1]
    )

    assert result.status is TokenValidationStatus.VALID
    assert (await repository.get_user(101)).status is AccessStatus.ACTIVE
    encrypted = await repository.get_encrypted_credential(101)
    assert encrypted != b"private-walt-token"
    assert cipher.decrypt(encrypted).get_secret_value() == "private-walt-token"


@pytest.mark.asyncio
async def test_failed_replacement_keeps_previous_ciphertext(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))
    await repository.set_encrypted_credential(ADMIN_TELEGRAM_ID, cipher.encrypt("old-token"))
    validator = AsyncMock(return_value=TokenValidationResult(TokenValidationStatus.INVALID))

    await activate_user_token(
        repository, cipher, validator, ADMIN_TELEGRAM_ID, SecretStr("bad-token"), SEED_ASSETS[1]
    )

    encrypted = await repository.get_encrypted_credential(ADMIN_TELEGRAM_ID)
    assert cipher.decrypt(encrypted).get_secret_value() == "old-token"


@pytest.mark.asyncio
async def test_token_message_is_deleted_even_when_validation_fails(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))
    message = SimpleNamespace(
        text="bad-token",
        delete=AsyncMock(),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=51)),
        from_user=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        chat=SimpleNamespace(id=ADMIN_TELEGRAM_ID),
        bot=SimpleNamespace(delete_message=AsyncMock()),
    )
    validator = AsyncMock(return_value=TokenValidationResult(TokenValidationStatus.INVALID))

    await process_token_message(
        message,
        repository,
        cipher,
        validator,
        ADMIN_TELEGRAM_ID,
        SEED_ASSETS[1],
    )

    message.delete.assert_awaited_once()
    assert "не принят" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_legacy_admin_token_migrates_once(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))

    assert await migrate_legacy_admin_token(repository, cipher, SecretStr("legacy-token")) is True
    assert await migrate_legacy_admin_token(repository, cipher, SecretStr("other-token")) is False
    encrypted = await repository.get_encrypted_credential(ADMIN_TELEGRAM_ID)
    assert cipher.decrypt(encrypted).get_secret_value() == "legacy-token"
