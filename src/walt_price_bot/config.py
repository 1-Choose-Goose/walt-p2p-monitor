from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: SecretStr = Field(min_length=20)
    credential_encryption_key: SecretStr
    walt_api_key: SecretStr | None = Field(default=None, min_length=10)
    database_path: Path = Path("data/walt-price-bot.db")
    owner_telegram_id: int | None = Field(default=None, gt=0)
    poll_interval_seconds: int = Field(default=30, ge=30, le=3600)
    timezone: str = "Asia/Yekaterinburg"

    @field_validator("credential_encryption_key")
    @classmethod
    def validate_credential_encryption_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("invalid Fernet key") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        return value
