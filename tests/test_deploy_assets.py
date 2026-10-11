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
    assert "replace-with-generated-fernet-key" in text
    assert "p2p.walletbot.me" not in text
    assert "DATABASE_PATH=/var/lib/walt-price-bot/walt-price-bot.db" in text


def test_installer_force_reinstalls_updated_application_package() -> None:
    text = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert "--force-reinstall --no-deps" in text
    assert 'cp -a "${source_dir}/src" "${preflight_dir}/src"' in text
    assert 'cp -a "${source_dir}/src" "${app_dir}/src"' in text
    assert "-name '*.egg-info' -exec rm -rf" in text
    assert "backup_sqlite_database" in text
    assert 'cp -a -- "${database_file}" "${backup_file}"' not in text
    assert "systemctl stop walt-price-bot.service" in text
    upgrade_phase = text.index('Settings(_env_file=Path(sys.argv[1]))')
    final_stop = text.index("systemctl stop walt-price-bot.service", upgrade_phase)
    final_backup = text.index("backup_file=${database_file}.pre-upgrade", upgrade_phase)
    assert final_stop < final_backup
    assert "asyncio.run(Repository(database).initialize())" in text
    assert 'PRAGMA integrity_check' in text


def test_installer_generates_encryption_key_once_and_protects_env_file() -> None:
    text = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert "grep -q '^CREDENTIAL_ENCRYPTION_KEY='" in text
    assert "Fernet.generate_key()" in text
    assert 'printf "\\nCREDENTIAL_ENCRYPTION_KEY=%s\\n"' in text
    assert 'chmod 0600 "${env_file}"' in text
    assert "set -x" not in text


def test_release_assets_are_lf_and_service_is_restarted() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_bytes()
    service = (ROOT / "deploy" / "walt-price-bot.service").read_bytes()

    assert b"\r\n" not in installer
    assert b"\r\n" not in service
    assert b"systemctl enable --now walt-price-bot.service" in installer


def test_installer_preflights_before_stopping_and_rolls_back_failure() -> None:
    text = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert text.index("preflight_dir=") < text.index(
        "systemctl stop walt-price-bot.service"
    )
    assert "rollback()" in text
    assert "trap rollback EXIT" in text
    assert "previous_release=" in text
    assert "failed_release=" in text
    assert "systemctl is-active --quiet walt-price-bot.service" in text
    assert "systemctl is-enabled --quiet walt-price-bot.service" in text
    assert "runuser -u waltbot" not in text
    assert "stable_checks" in text
    assert "systemctl show -p SubState" in text
    assert "sleep 2" not in text
