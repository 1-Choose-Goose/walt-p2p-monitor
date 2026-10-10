import configparser
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_systemd_service_is_non_root_and_hardened() -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(ROOT / "deploy" / "walt-price-bot.service", encoding="utf-8")

    service = parser["Service"]
    assert service["User"] == "waltbot"
    assert service["EnvironmentFile"] == "/etc/walt-price-bot.env"
    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["ReadWritePaths"] == "/var/lib/walt-price-bot"
    assert service["UMask"] == "0077"


def test_environment_template_contains_only_placeholders() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "replace-with-bot-token" in text
    assert "replace-with-walt-api-key" in text
    assert "p2p.walletbot.me" not in text
