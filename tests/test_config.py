from pathlib import Path

import pytest
from pydantic import ValidationError

from walt_price_bot.config import Settings


def test_settings_require_both_secrets(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_path=tmp_path / "bot.db")


def test_settings_never_include_secrets_in_repr(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="test-token-not-a-real-secret-value",
        walt_api_key="walt-super-secret-key",
        database_path=tmp_path / "bot.db",
    )

    rendered = repr(settings)
    assert "abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "walt-super-secret-key" not in rendered
    assert settings.poll_interval_seconds == 30


def test_settings_reject_unknown_timezone(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Settings(
            _env_file=None,
            telegram_bot_token="test-token-not-a-real-secret-value",
            walt_api_key="walt-super-secret-key",
            database_path=tmp_path / "bot.db",
            timezone="Invalid/Timezone",
        )
