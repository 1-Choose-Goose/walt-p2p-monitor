from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Contact
from cryptography.fernet import Fernet
from pydantic import SecretStr

from walt_price_bot.credentials import CredentialCipher
from walt_price_bot.models import SEED_ASSETS, AccessStatus
from walt_price_bot.onboarding import (
    activate_user_token,
    approve_access,
    migrate_legacy_admin_token,
    normalize_phone,
    process_token_message,
    register_contact,
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
    message = SimpleNamespace(text="bad-token", delete=AsyncMock(), answer=AsyncMock())
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
