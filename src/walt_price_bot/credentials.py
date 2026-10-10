from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class CredentialDecryptionError(ValueError):
    pass


class CredentialCipher:
    __slots__ = ("_fernet",)

    def __init__(self, key: SecretStr) -> None:
        try:
            self._fernet = Fernet(key.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("invalid credential encryption key") from exc

    def encrypt(self, token: str) -> bytes:
        if not token:
            raise ValueError("credential cannot be empty")
        return self._fernet.encrypt(token.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> SecretStr:
        try:
            value = self._fernet.decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise CredentialDecryptionError("stored credential cannot be decrypted") from exc
        return SecretStr(value)

    def __repr__(self) -> str:
        return "CredentialCipher([REDACTED])"

