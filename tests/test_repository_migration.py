import sqlite3
from pathlib import Path

import pytest

from walt_price_bot import schema
from walt_price_bot.models import Asset, QuoteSide
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, Repository


def create_v14_database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE settings (
                singleton INTEGER PRIMARY KEY,
                owner_id INTEGER,
                paused INTEGER NOT NULL DEFAULT 0,
                last_message_id INTEGER
            );
            INSERT INTO settings VALUES (1, 101, 1, 777);
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                asset TEXT NOT NULL,
                quote_side TEXT NOT NULL,
                threshold_direction TEXT,
                target TEXT NOT NULL,
                baseline TEXT,
                armed INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO alerts(kind, asset, quote_side, threshold_direction, target)
            VALUES ('threshold', 'TON', 'BUY', 'below', '100');
            CREATE TABLE schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                interval_hours INTEGER,
                daily_time TEXT,
                last_sent_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO schedules(kind, interval_hours) VALUES ('interval', 6);
            CREATE TABLE opportunity_settings (
                singleton INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL,
                mode TEXT NOT NULL,
                min_spread_percent TEXT NOT NULL
            );
            INSERT INTO opportunity_settings VALUES (1, 1, 'BOTH', '2');
            CREATE TABLE opportunity_notifications (
                asset TEXT NOT NULL,
                quote_side TEXT NOT NULL,
                last_ad_id TEXT NOT NULL,
                PRIMARY KEY(asset, quote_side)
            );
            INSERT INTO opportunity_notifications VALUES ('TON', 'BUY', 'offer-1');
            CREATE TABLE opportunity_notification_history (
                asset TEXT NOT NULL,
                quote_side TEXT NOT NULL,
                ad_id TEXT NOT NULL,
                notified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(asset, quote_side, ad_id)
            );
            INSERT INTO opportunity_notification_history(asset, quote_side, ad_id)
            VALUES ('TON', 'BUY', 'offer-2');
            """
        )


def create_v4_database(path: Path) -> None:
    current_history_table = next(
        statement
        for statement in schema.CREATE_STATEMENTS
        if "CREATE TABLE opportunity_notification_history" in statement
    )
    v4_history_table = current_history_table.replace(
        "        message_text TEXT NOT NULL DEFAULT '',\n"
        "        expires_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',\n",
        "",
    )
    with sqlite3.connect(path) as db:
        for statement in schema.CREATE_STATEMENTS:
            db.execute(
                v4_history_table if statement == current_history_table else statement
            )
        db.execute(
            "INSERT INTO schema_meta(singleton, version) VALUES (1, 4)"
        )


@pytest.mark.asyncio
async def test_v4_database_adds_expiring_opportunity_history(tmp_path: Path) -> None:
    database = tmp_path / "v4.db"
    create_v4_database(database)

    await Repository(database).initialize()

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone() == (5,)
        columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(opportunity_notification_history)"
            )
        }
        assert {"message_text", "expires_at"} <= columns


@pytest.mark.asyncio
async def test_v14_migration_is_complete_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    create_v14_database(database)
    repository = Repository(database)

    await repository.initialize()
    await repository.initialize()

    assert await repository.get_owner_id() == ADMIN_TELEGRAM_ID
    assert await repository.is_paused(ADMIN_TELEGRAM_ID) is True
    assert await repository.get_last_message_id(ADMIN_TELEGRAM_ID) == 777
    alerts = await repository.list_alerts(ADMIN_TELEGRAM_ID)
    assert [(rule.asset, rule.target) for rule in alerts] == [(Asset.GRAM, "100")]
    assert len(await repository.list_schedules(ADMIN_TELEGRAM_ID)) == 1
    assert not await repository.was_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.GRAM, QuoteSide.BUY, "offer-1"
    )
    assert not await repository.was_opportunity_notified(
        ADMIN_TELEGRAM_ID, Asset.GRAM, QuoteSide.BUY, "offer-2"
    )
    assert await repository.list_opportunity_history(ADMIN_TELEGRAM_ID) == []

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone() == (5,)
        assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (1,)
        columns = {row[1] for row in db.execute("PRAGMA table_info(user_credentials)")}
        assert "encrypted_token" in columns
        foreign_keys = db.execute("PRAGMA foreign_key_list(alerts)").fetchall()
        assert any(row[2] == "users" and row[6] == "CASCADE" for row in foreign_keys)


@pytest.mark.asyncio
async def test_failed_legacy_copy_rolls_back_entire_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "legacy.db"
    create_v14_database(database)

    original = schema._copy_legacy_data

    async def fail_after_copy(*args: object, **kwargs: object) -> None:
        await original(*args, **kwargs)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(schema, "_copy_legacy_data", fail_after_copy)

    with pytest.raises(RuntimeError, match="injected"):
        await Repository(database).initialize()

    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM alerts WHERE asset = 'TON'"
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone() == (0,)

