from collections.abc import Iterable

import aiosqlite

from .models import SEED_ASSETS

CURRENT_SCHEMA_VERSION = 3
LEGACY_TABLES = (
    "settings",
    "alerts",
    "schedules",
    "opportunity_settings",
    "opportunity_notifications",
    "opportunity_notification_history",
)

CREATE_STATEMENTS = (
    """
    CREATE TABLE schema_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE users (
        telegram_id INTEGER PRIMARY KEY,
        phone TEXT UNIQUE,
        first_name TEXT,
        username TEXT,
        role TEXT NOT NULL CHECK (role IN ('ADMIN', 'USER', 'PREMIUM')),
        status TEXT NOT NULL CHECK (
            status IN ('PENDING', 'AWAITING_TOKEN', 'ACTIVE', 'REJECTED', 'REVOKED')
        ),
        version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE user_credentials (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        encrypted_token BLOB NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE user_settings (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
        last_message_id INTEGER,
        last_monitor_at TEXT,
        last_summary_at TEXT
    )
    """,
    """
    CREATE TABLE user_preferences (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        directions TEXT NOT NULL DEFAULT '["BUY","SELL"]',
        min_limit_from TEXT NOT NULL DEFAULT '500',
        min_limit_to TEXT DEFAULT '2000',
        desired_amount TEXT,
        buy_payments TEXT NOT NULL DEFAULT '["sbp","sberbankru","russian_banks"]',
        sell_payments TEXT NOT NULL DEFAULT '["any"]',
        monitor_interval_seconds INTEGER NOT NULL DEFAULT 30,
        summary_interval_minutes INTEGER NOT NULL DEFAULT 60,
        summary_enabled INTEGER NOT NULL DEFAULT 0 CHECK (summary_enabled IN (0, 1)),
        timezone TEXT NOT NULL DEFAULT 'Asia/Yekaterinburg',
        show_exchange_rate INTEGER NOT NULL DEFAULT 1 CHECK (show_exchange_rate IN (0, 1)),
        notify_threshold INTEGER NOT NULL DEFAULT 1 CHECK (notify_threshold IN (0, 1)),
        notify_percent INTEGER NOT NULL DEFAULT 1 CHECK (notify_percent IN (0, 1)),
        notify_scheduled INTEGER NOT NULL DEFAULT 1 CHECK (notify_scheduled IN (0, 1)),
        notify_opportunity INTEGER NOT NULL DEFAULT 1 CHECK (notify_opportunity IN (0, 1)),
        quiet_enabled INTEGER NOT NULL DEFAULT 0 CHECK (quiet_enabled IN (0, 1)),
        quiet_start TEXT NOT NULL DEFAULT '22:00',
        quiet_end TEXT NOT NULL DEFAULT '08:00'
    )
    """,
    """
    CREATE TABLE asset_catalog (
        symbol TEXT PRIMARY KEY,
        walt_code TEXT NOT NULL,
        exchange_code TEXT,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE user_assets (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        symbol TEXT NOT NULL REFERENCES asset_catalog(symbol),
        PRIMARY KEY (telegram_id, symbol)
    )
    """,
    """
    CREATE TABLE ui_sessions (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        state TEXT NOT NULL,
        data_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('threshold', 'percent')),
        asset TEXT NOT NULL REFERENCES asset_catalog(symbol),
        quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
        threshold_direction TEXT CHECK (threshold_direction IN ('above', 'below')),
        target TEXT NOT NULL,
        baseline TEXT,
        armed INTEGER NOT NULL DEFAULT 1 CHECK (armed IN (0, 1)),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
    )
    """,
    """
    CREATE TABLE schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('interval', 'daily')),
        interval_hours INTEGER,
        daily_time TEXT,
        last_sent_at TEXT,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
    )
    """,
    """
    CREATE TABLE opportunity_settings (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
        mode TEXT NOT NULL DEFAULT 'BOTH' CHECK (mode IN ('BUY', 'SELL', 'BOTH')),
        min_spread_percent TEXT NOT NULL DEFAULT '2'
    )
    """,
    """
    CREATE TABLE opportunity_notifications (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        asset TEXT NOT NULL REFERENCES asset_catalog(symbol),
        quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
        last_ad_id TEXT NOT NULL,
        PRIMARY KEY (telegram_id, asset, quote_side)
    )
    """,
    """
    CREATE TABLE opportunity_notification_history (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        asset TEXT NOT NULL REFERENCES asset_catalog(symbol),
        quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
        ad_id TEXT NOT NULL,
        notified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (telegram_id, asset, quote_side, ad_id)
    )
    """,
)


async def _table_names(db: aiosqlite.Connection) -> set[str]:
    rows = await (
        await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ).fetchall()
    return {str(row[0]) for row in rows}


async def _seed_assets(db: aiosqlite.Connection) -> None:
    await db.executemany(
        """
        INSERT INTO asset_catalog(symbol, walt_code, exchange_code, active, sort_order)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            walt_code = excluded.walt_code,
            exchange_code = excluded.exchange_code,
            sort_order = excluded.sort_order
        """,
        [
            (
                entry.symbol.value,
                entry.walt_code,
                entry.exchange_code,
                int(entry.active),
                entry.sort_order,
            )
            for entry in SEED_ASSETS
        ],
    )


async def _ensure_user_defaults(db: aiosqlite.Connection, telegram_id: int) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO user_settings(telegram_id) VALUES (?)", (telegram_id,)
    )
    await db.execute(
        "INSERT OR IGNORE INTO user_preferences(telegram_id) VALUES (?)", (telegram_id,)
    )
    await db.execute(
        "INSERT OR IGNORE INTO opportunity_settings(telegram_id) VALUES (?)", (telegram_id,)
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO user_assets(telegram_id, symbol)
        SELECT ?, symbol FROM asset_catalog WHERE active = 1
        """,
        (telegram_id,),
    )


async def _copy_legacy_data(
    db: aiosqlite.Connection, legacy_tables: Iterable[str], admin_id: int
) -> None:
    legacy = set(legacy_tables)
    if "settings" in legacy:
        columns = {
            row[1]
            for row in await (
                await db.execute("PRAGMA table_info(settings_legacy)")
            ).fetchall()
        }
        last_message = "last_message_id" if "last_message_id" in columns else "NULL"
        await db.execute(
            f"""
            UPDATE user_settings
            SET paused = COALESCE((SELECT paused FROM settings_legacy WHERE singleton = 1), 0),
                last_message_id = (SELECT {last_message} FROM settings_legacy WHERE singleton = 1)
            WHERE telegram_id = ?
            """,
            (admin_id,),
        )
    if "alerts" in legacy:
        await db.execute(
            """
            INSERT INTO alerts(
                id, telegram_id, kind, asset, quote_side, threshold_direction,
                target, baseline, armed, enabled
            )
            SELECT id, ?, kind, CASE asset WHEN 'TON' THEN 'GRAM' ELSE asset END,
                   quote_side, threshold_direction, target, baseline, armed, enabled
            FROM alerts_legacy
            """,
            (admin_id,),
        )
    if "schedules" in legacy:
        await db.execute(
            """
            INSERT INTO schedules(
                id, telegram_id, kind, interval_hours, daily_time, last_sent_at, enabled
            )
            SELECT id, ?, kind, interval_hours, daily_time, last_sent_at, enabled
            FROM schedules_legacy
            """,
            (admin_id,),
        )
    if "opportunity_settings" in legacy:
        await db.execute(
            """
            UPDATE opportunity_settings SET
                enabled = COALESCE((SELECT enabled FROM opportunity_settings_legacy LIMIT 1), 0),
                mode = COALESCE((SELECT mode FROM opportunity_settings_legacy LIMIT 1), 'BOTH'),
                min_spread_percent = COALESCE(
                    (SELECT min_spread_percent FROM opportunity_settings_legacy LIMIT 1), '2'
                )
            WHERE telegram_id = ?
            """,
            (admin_id,),
        )
    if "opportunity_notifications" in legacy:
        await db.execute(
            """
            INSERT OR IGNORE INTO opportunity_notifications(
                telegram_id, asset, quote_side, last_ad_id
            )
            SELECT ?, CASE asset WHEN 'TON' THEN 'GRAM' ELSE asset END,
                   quote_side, last_ad_id
            FROM opportunity_notifications_legacy
            """,
            (admin_id,),
        )
        await db.execute(
            """
            INSERT OR IGNORE INTO opportunity_notification_history(
                telegram_id, asset, quote_side, ad_id
            )
            SELECT ?, CASE asset WHEN 'TON' THEN 'GRAM' ELSE asset END,
                   quote_side, last_ad_id
            FROM opportunity_notifications_legacy
            """,
            (admin_id,),
        )
    if "opportunity_notification_history" in legacy:
        await db.execute(
            """
            INSERT OR IGNORE INTO opportunity_notification_history(
                telegram_id, asset, quote_side, ad_id, notified_at
            )
            SELECT ?, CASE asset WHEN 'TON' THEN 'GRAM' ELSE asset END,
                   quote_side, ad_id, notified_at
            FROM opportunity_notification_history_legacy
            """,
            (admin_id,),
        )


async def _verify_counts(
    db: aiosqlite.Connection, legacy_tables: Iterable[str], admin_id: int
) -> None:
    legacy = set(legacy_tables)
    for table in ("alerts", "schedules"):
        if table not in legacy:
            continue
        old_count = (
            await (await db.execute(f"SELECT COUNT(*) FROM {table}_legacy")).fetchone()
        )[0]
        new_count = (
            await (
                await db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE telegram_id = ?", (admin_id,)
                )
            ).fetchone()
        )[0]
        if old_count != new_count:
            raise RuntimeError(f"{table} migration count mismatch")
    history_sources: list[str] = []
    if "opportunity_notifications" in legacy:
        history_sources.append(
            "SELECT CASE asset WHEN 'TON' THEN 'GRAM' ELSE asset END AS asset, "
            "quote_side, last_ad_id AS ad_id FROM opportunity_notifications_legacy"
        )
    if "opportunity_notification_history" in legacy:
        history_sources.append(
            "SELECT CASE asset WHEN 'TON' THEN 'GRAM' ELSE asset END AS asset, "
            "quote_side, ad_id FROM opportunity_notification_history_legacy"
        )
    if history_sources:
        expected = (
            await (
                await db.execute(
                    f"SELECT COUNT(*) FROM ({' UNION '.join(history_sources)})"
                )
            ).fetchone()
        )[0]
        actual = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM opportunity_notification_history "
                    "WHERE telegram_id = ?",
                    (admin_id,),
                )
            ).fetchone()
        )[0]
        if expected != actual:
            raise RuntimeError("opportunity history migration count mismatch")


async def migrate_database(db: aiosqlite.Connection, admin_id: int) -> None:
    await db.execute("PRAGMA busy_timeout=5000")
    existing = await _table_names(db)
    if "schema_meta" in existing:
        row = await (
            await db.execute("SELECT version FROM schema_meta WHERE singleton = 1")
        ).fetchone()
        if row is None or int(row[0]) not in {2, CURRENT_SCHEMA_VERSION}:
            raise RuntimeError("unsupported database schema version")
        await db.execute("BEGIN IMMEDIATE")
        try:
            if int(row[0]) == 2:
                await db.execute("ALTER TABLE user_settings ADD COLUMN last_monitor_at TEXT")
                await db.execute("ALTER TABLE user_settings ADD COLUMN last_summary_at TEXT")
                await db.execute(
                    "UPDATE schema_meta SET version = ? WHERE singleton = 1",
                    (CURRENT_SCHEMA_VERSION,),
                )
            await _seed_assets(db)
            await db.execute(
                """
                INSERT OR IGNORE INTO users(telegram_id, role, status)
                VALUES (?, 'ADMIN', 'ACTIVE')
                """,
                (admin_id,),
            )
            await _ensure_user_defaults(db, admin_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return

    legacy = {name for name in LEGACY_TABLES if name in existing}
    await db.execute("PRAGMA foreign_keys=OFF")
    await db.execute("BEGIN IMMEDIATE")
    try:
        for table in legacy:
            await db.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
        for statement in CREATE_STATEMENTS:
            await db.execute(statement)
        await db.execute(
            "INSERT INTO schema_meta(singleton, version) VALUES (1, ?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        await _seed_assets(db)
        await db.execute(
            """
            INSERT INTO users(telegram_id, role, status)
            VALUES (?, 'ADMIN', 'ACTIVE')
            """,
            (admin_id,),
        )
        await _ensure_user_defaults(db, admin_id)
        await _copy_legacy_data(db, legacy, admin_id)
        await _verify_counts(db, legacy, admin_id)
        for table in legacy:
            await db.execute(f"DROP TABLE {table}_legacy")
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.execute("PRAGMA foreign_keys=ON")

