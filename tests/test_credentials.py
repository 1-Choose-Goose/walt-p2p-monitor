import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from walt_price_bot.credentials import CredentialCipher, CredentialDecryptionError


def test_credential_cipher_round_trip_and_randomized_ciphertext() -> None:
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode("ascii")))

    first = cipher.encrypt("walt-secret-token")
    second = cipher.encrypt("walt-secret-token")

    assert first != second
    assert b"walt-secret-token" not in first
    assert cipher.decrypt(first).get_secret_value() == "walt-secret-token"


def test_credential_cipher_rejects_invalid_key_without_echoing_it() -> None:
    key = "invalid-master-key"

    with pytest.raises(ValueError) as error:
        CredentialCipher(SecretStr(key))

    assert key not in str(error.value)


def test_credential_cipher_rejects_corrupted_value_without_echoing_it() -> None:
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode("ascii")))
    corrupted = b"corrupted-user-ciphertext"

    with pytest.raises(CredentialDecryptionError) as error:
        cipher.decrypt(corrupted)

    assert corrupted.decode() not in str(error.value)


def test_credential_cipher_repr_contains_no_key_or_token() -> None:
    key = Fernet.generate_key().decode("ascii")
    cipher = CredentialCipher(SecretStr(key))
    ciphertext = cipher.encrypt("walt-secret-token")

    rendered = repr(cipher)
    assert key not in rendered
    assert "walt-secret-token" not in rendered
    assert ciphertext.decode("ascii") not in rendered

