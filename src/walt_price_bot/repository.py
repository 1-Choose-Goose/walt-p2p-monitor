from decimal import Decimal
from pathlib import Path

import aiosqlite

from .models import (
    AlertKind,
    AlertRule,
    Asset,
    OpportunityMode,
    OpportunitySettings,
    QuoteSide,
    ScheduleKind,
    ScheduleRule,
    ThresholdDirection,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    owner_id INTEGER,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    last_message_id INTEGER
);
INSERT OR IGNORE INTO settings(singleton) VALUES (1);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('threshold', 'percent')),
    asset TEXT NOT NULL CHECK (asset IN ('USDT', 'TON', 'BTC')),
    quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
    threshold_direction TEXT CHECK (threshold_direction IN ('above', 'below')),
    target TEXT NOT NULL,
    baseline TEXT,
    armed INTEGER NOT NULL DEFAULT 1 CHECK (armed IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('interval', 'daily')),
    interval_hours INTEGER,
    daily_time TEXT,
    last_sent_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);
CREATE TABLE IF NOT EXISTS opportunity_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    mode TEXT NOT NULL DEFAULT 'BOTH' CHECK (mode IN ('BUY', 'SELL', 'BOTH')),
    min_spread_percent TEXT NOT NULL DEFAULT '2'
);
INSERT OR IGNORE INTO opportunity_settings(singleton) VALUES (1);
CREATE TABLE IF NOT EXISTS opportunity_notifications (
    asset TEXT NOT NULL CHECK (asset IN ('USDT', 'TON', 'BTC')),
    quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
    last_ad_id TEXT NOT NULL,
    PRIMARY KEY (asset, quote_side)
);
"""


class Repository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(settings)")).fetchall()
            }
            if "last_message_id" not in columns:
                await db.execute("ALTER TABLE settings ADD COLUMN last_message_id INTEGER")
            alerts_sql = await self._scalar(
                db, "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'alerts'"
            )
            if alerts_sql is not None and "'BTC'" not in alerts_sql:
                await db.executescript(
                    """
                    ALTER TABLE alerts RENAME TO alerts_legacy;
                    CREATE TABLE alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL CHECK (kind IN ('threshold', 'percent')),
                        asset TEXT NOT NULL CHECK (asset IN ('USDT', 'TON', 'BTC')),
                        quote_side TEXT NOT NULL CHECK (quote_side IN ('BUY', 'SELL')),
                        threshold_direction TEXT CHECK (threshold_direction IN ('above', 'below')),
                        target TEXT NOT NULL,
                        baseline TEXT,
                        armed INTEGER NOT NULL DEFAULT 1 CHECK (armed IN (0, 1)),
                        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
                    );
                    INSERT INTO alerts
                        (id, kind, asset, quote_side, threshold_direction, target,
                         baseline, armed, enabled)
                    SELECT id, kind, asset, quote_side, threshold_direction, target,
                           baseline, armed, enabled
                    FROM alerts_legacy;
                    DROP TABLE alerts_legacy;
                    """
                )
            await db.commit()

    async def claim_owner(self, telegram_id: int) -> bool:
        if telegram_id <= 0:
            raise ValueError("telegram_id must be positive")
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE settings SET owner_id = ? WHERE singleton = 1 AND owner_id IS NULL",
                (telegram_id,),
            )
            await db.commit()
            if cursor.rowcount == 1:
                return True
            current = await self._scalar(db, "SELECT owner_id FROM settings WHERE singleton = 1")
            return current == telegram_id

    async def set_configured_owner(self, telegram_id: int) -> None:
        if telegram_id <= 0:
            raise ValueError("telegram_id must be positive")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE settings SET owner_id = ? WHERE singleton = 1 AND owner_id IS NULL",
                (telegram_id,),
            )
            await db.commit()

    async def get_owner_id(self) -> int | None:
        async with aiosqlite.connect(self.path) as db:
            return await self._scalar(db, "SELECT owner_id FROM settings WHERE singleton = 1")

    async def is_owner(self, telegram_id: int) -> bool:
        return await self.get_owner_id() == telegram_id

    async def set_paused(self, paused: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE settings SET paused = ? WHERE singleton = 1",
                (int(paused),),
            )
            await db.commit()

    async def is_paused(self) -> bool:
        async with aiosqlite.connect(self.path) as db:
            value = await self._scalar(db, "SELECT paused FROM settings WHERE singleton = 1")
            return bool(value)

    async def get_last_message_id(self) -> int | None:
        async with aiosqlite.connect(self.path) as db:
            return await self._scalar(
                db, "SELECT last_message_id FROM settings WHERE singleton = 1"
            )

    async def set_last_message_id(self, message_id: int | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE settings SET last_message_id = ? WHERE singleton = 1",
                (message_id,),
            )
            await db.commit()

    async def add_alert(
        self,
        *,
        kind: AlertKind,
        asset: Asset,
        quote_side: QuoteSide,
        target: str,
        threshold_direction: ThresholdDirection | None = None,
        baseline: str | None = None,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO alerts("
                "kind, asset, quote_side, threshold_direction, target, baseline"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    kind.value,
                    asset.value,
                    quote_side.value,
                    threshold_direction.value if threshold_direction else None,
                    target,
                    baseline,
                ),
            )
            await db.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("failed to create alert")
            return cursor.lastrowid

    async def list_alerts(self, *, enabled_only: bool = False) -> list[AlertRule]:
        query = "SELECT * FROM alerts"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query)).fetchall()
        return [self._alert_from_row(row) for row in rows]

    async def delete_alert(self, alert_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            await db.commit()
            return cursor.rowcount == 1

    async def update_alert_state(
        self, alert_id: int, *, armed: bool | None = None, baseline: str | None = None
    ) -> None:
        assignments: list[str] = []
        values: list[object] = []
        if armed is not None:
            assignments.append("armed = ?")
            values.append(int(armed))
        if baseline is not None:
            assignments.append("baseline = ?")
            values.append(baseline)
        if not assignments:
            return
        values.append(alert_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE alerts SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608
                values,
            )
            await db.commit()

    async def add_schedule(
        self,
        *,
        kind: ScheduleKind,
        interval_hours: int | None = None,
        daily_time: str | None = None,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO schedules(kind, interval_hours, daily_time) VALUES (?, ?, ?)",
                (kind.value, interval_hours, daily_time),
            )
            await db.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("failed to create schedule")
            return cursor.lastrowid

    async def list_schedules(self, *, enabled_only: bool = False) -> list[ScheduleRule]:
        query = "SELECT * FROM schedules"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query)).fetchall()
        return [
            ScheduleRule(
                id=row["id"],
                kind=ScheduleKind(row["kind"]),
                interval_hours=row["interval_hours"],
                daily_time=row["daily_time"],
                last_sent_at=row["last_sent_at"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    async def mark_schedule_sent(self, schedule_id: int, sent_at: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE schedules SET last_sent_at = ? WHERE id = ?",
                (sent_at, schedule_id),
            )
            await db.commit()

    async def delete_schedule(self, schedule_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            await db.commit()
            return cursor.rowcount == 1

    async def get_opportunity_settings(self) -> OpportunitySettings:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT enabled, mode, min_spread_percent "
                    "FROM opportunity_settings WHERE singleton = 1"
                )
            ).fetchone()
        if row is None:
            raise RuntimeError("opportunity settings are not initialized")
        return OpportunitySettings(
            enabled=bool(row["enabled"]),
            mode=OpportunityMode(row["mode"]),
            min_spread_percent=Decimal(row["min_spread_percent"]),
        )

    async def configure_opportunities(
        self, *, mode: OpportunityMode, min_spread_percent: Decimal
    ) -> None:
        if (
            not min_spread_percent.is_finite()
            or min_spread_percent <= 0
            or min_spread_percent > Decimal("100")
        ):
            raise ValueError("min_spread_percent must be between 0 and 100")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE opportunity_settings "
                "SET enabled = 1, mode = ?, min_spread_percent = ? WHERE singleton = 1",
                (mode.value, str(min_spread_percent)),
            )
            await db.execute("DELETE FROM opportunity_notifications")
            await db.commit()

    async def disable_opportunities(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE opportunity_settings SET enabled = 0 WHERE singleton = 1"
            )
            await db.commit()

    async def get_last_opportunity_ad(self, asset: Asset, side: QuoteSide) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT last_ad_id FROM opportunity_notifications "
                    "WHERE asset = ? AND quote_side = ?",
                    (asset.value, side.value),
                )
            ).fetchone()
        return row[0] if row else None

    async def mark_opportunity_notified(
        self, asset: Asset, side: QuoteSide, ad_id: str
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO opportunity_notifications(asset, quote_side, last_ad_id) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(asset, quote_side) DO UPDATE SET last_ad_id = excluded.last_ad_id",
                (asset.value, side.value, ad_id),
            )
            await db.commit()

    @staticmethod
    async def _scalar(db: aiosqlite.Connection, query: str) -> object | None:
        row = await (await db.execute(query)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _alert_from_row(row: aiosqlite.Row) -> AlertRule:
        direction = row["threshold_direction"]
        return AlertRule(
            id=row["id"],
            kind=AlertKind(row["kind"]),
            asset=Asset(row["asset"]),
            quote_side=QuoteSide(row["quote_side"]),
            threshold_direction=ThresholdDirection(direction) if direction else None,
            target=row["target"],
            baseline=row["baseline"],
            armed=bool(row["armed"]),
            enabled=bool(row["enabled"]),
        )
