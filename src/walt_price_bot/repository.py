from pathlib import Path

import aiosqlite

from .models import (
    AlertKind,
    AlertRule,
    Asset,
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
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1))
);
INSERT OR IGNORE INTO settings(singleton) VALUES (1);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('threshold', 'percent')),
    asset TEXT NOT NULL CHECK (asset IN ('USDT', 'TON')),
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
"""


class Repository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
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
