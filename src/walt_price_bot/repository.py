import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import aiosqlite

from . import schema
from .models import (
    AccessStatus,
    AlertKind,
    AlertRule,
    Asset,
    AssetCatalogEntry,
    NotificationPreferences,
    OpportunityHistoryEntry,
    OpportunityMode,
    OpportunitySettings,
    QuietHours,
    QuoteSide,
    ScheduleKind,
    ScheduleRule,
    ThresholdDirection,
    User,
    UserPreferences,
    UserRole,
)

ADMIN_TELEGRAM_ID = 768929900
OPPORTUNITY_HISTORY_TTL = timedelta(minutes=10)


class DuplicatePhoneError(ValueError):
    pass


class InvalidUserTransitionError(ValueError):
    pass


class Repository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.opportunity_delivery_lock = asyncio.Lock()
        self.message_delivery_lock = asyncio.Lock()
        self._user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._opportunity_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await schema.migrate_database(db, ADMIN_TELEGRAM_ID)

    def user_lock(self, telegram_id: int) -> asyncio.Lock:
        return self._user_locks[telegram_id]

    def opportunity_lock(self, telegram_id: int) -> asyncio.Lock:
        return self._opportunity_locks[telegram_id]

    async def claim_owner(self, telegram_id: int) -> bool:
        return telegram_id == ADMIN_TELEGRAM_ID

    async def set_configured_owner(self, telegram_id: int) -> None:
        if telegram_id != ADMIN_TELEGRAM_ID:
            raise RuntimeError("configured owner must match the fixed administrator")

    async def get_owner_id(self) -> int:
        return ADMIN_TELEGRAM_ID

    async def is_owner(self, telegram_id: int) -> bool:
        return telegram_id == ADMIN_TELEGRAM_ID

    async def get_user(self, telegram_id: int) -> User | None:
        async with self._connect() as db:
            row = await (
                await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            ).fetchone()
        return self._user_from_row(row) if row else None

    async def create_or_refresh_request(
        self,
        telegram_id: int,
        *,
        phone: str,
        first_name: str | None,
        username: str | None,
    ) -> User:
        if telegram_id <= 0:
            raise ValueError("telegram_id must be positive")
        async with self.user_lock(telegram_id), self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    INSERT INTO users(telegram_id, phone, first_name, username, role, status)
                    VALUES (?, ?, ?, ?, 'USER', 'PENDING')
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        phone = excluded.phone,
                        first_name = excluded.first_name,
                        username = excluded.username,
                        status = CASE
                            WHEN users.status = 'REJECTED' THEN 'PENDING'
                            ELSE users.status
                        END,
                        version = users.version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (telegram_id, phone, first_name, username),
                )
                await self._insert_user_defaults(db, telegram_id)
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                if "phone" in str(exc).lower() or "unique" in str(exc).lower():
                    raise DuplicatePhoneError("phone is already registered") from None
                raise
            row = await (
                await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to create access request")
        return self._user_from_row(row)

    async def transition_user(
        self,
        telegram_id: int,
        *,
        expected: AccessStatus,
        new_status: AccessStatus,
        expected_version: int | None = None,
    ) -> User:
        if telegram_id == ADMIN_TELEGRAM_ID:
            raise InvalidUserTransitionError("administrator status is immutable")
        async with self.user_lock(telegram_id), self._connect() as db:
            version_clause = " AND version = ?" if expected_version is not None else ""
            params: tuple[object, ...] = (
                new_status.value,
                telegram_id,
                expected.value,
                *((expected_version,) if expected_version is not None else ()),
            )
            cursor = await db.execute(
                """
                UPDATE users SET status = ?, version = version + 1,
                                 updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ? AND status = ?
                """
                + version_clause,
                params,
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise InvalidUserTransitionError("user state has changed")
            await db.commit()
            row = await (
                await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            ).fetchone()
        if row is None:
            raise InvalidUserTransitionError("user does not exist")
        return self._user_from_row(row)

    async def set_user_role(
        self,
        telegram_id: int,
        role: UserRole,
        *,
        expected_version: int | None = None,
    ) -> User:
        if telegram_id == ADMIN_TELEGRAM_ID or role is UserRole.ADMIN:
            raise InvalidUserTransitionError("administrator role is immutable")
        async with self._connect() as db:
            version_clause = " AND version = ?" if expected_version is not None else ""
            params: tuple[object, ...] = (
                role.value,
                telegram_id,
                *((expected_version,) if expected_version is not None else ()),
            )
            cursor = await db.execute(
                """
                UPDATE users SET role = ?, version = version + 1,
                                 updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """
                + version_clause,
                params,
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise InvalidUserTransitionError("user state has changed")
            await db.commit()
            row = await (
                await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            ).fetchone()
        return self._user_from_row(row)

    async def list_users(
        self,
        *,
        status: AccessStatus | None = None,
        limit: int = 10,
        offset: int = 0,
        exclude_telegram_id: int | None = None,
    ) -> list[User]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("invalid pagination")
        query = "SELECT * FROM users"
        params: list[object] = []
        conditions: list[str] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if exclude_telegram_id is not None:
            conditions.append("telegram_id <> ?")
            params.append(exclude_telegram_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY telegram_id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        async with self._connect() as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [self._user_from_row(row) for row in rows]

    async def count_users(
        self,
        *,
        status: AccessStatus | None = None,
        exclude_telegram_id: int | None = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM users"
        params: list[object] = []
        conditions: list[str] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if exclude_telegram_id is not None:
            conditions.append("telegram_id <> ?")
            params.append(exclude_telegram_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        async with self._connect() as db:
            return int(await self._scalar(db, query, tuple(params)))

    async def delete_user(self, telegram_id: int) -> bool:
        if telegram_id == ADMIN_TELEGRAM_ID:
            return False
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
            await db.commit()
            return cursor.rowcount == 1

    async def set_encrypted_credential(self, telegram_id: int, ciphertext: bytes) -> None:
        if not ciphertext:
            raise ValueError("ciphertext cannot be empty")
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO user_credentials(telegram_id, encrypted_token) VALUES (?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    encrypted_token = excluded.encrypted_token,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, ciphertext),
            )
            await db.commit()

    async def get_encrypted_credential(self, telegram_id: int) -> bytes | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT encrypted_token FROM user_credentials WHERE telegram_id = ?",
                    (telegram_id,),
                )
            ).fetchone()
        return bytes(row[0]) if row else None

    async def set_paused(self, telegram_id: int, paused: bool) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE user_settings SET paused = ? WHERE telegram_id = ?",
                (int(paused), telegram_id),
            )
            await db.commit()

    async def is_paused(self, telegram_id: int) -> bool:
        async with self._connect() as db:
            value = await self._scalar(
                db, "SELECT paused FROM user_settings WHERE telegram_id = ?", (telegram_id,)
            )
        return bool(value)

    async def get_last_message_id(self, telegram_id: int) -> int | None:
        async with self._connect() as db:
            value = await self._scalar(
                db,
                "SELECT last_message_id FROM user_settings WHERE telegram_id = ?",
                (telegram_id,),
            )
        return int(value) if value is not None else None

    async def set_last_message_id(self, telegram_id: int, message_id: int | None) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE user_settings SET last_message_id = ? WHERE telegram_id = ?",
                (message_id, telegram_id),
            )
            await db.commit()

    async def get_delivery_times(self, telegram_id: int) -> tuple[str | None, str | None]:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT last_monitor_at, last_summary_at FROM user_settings "
                    "WHERE telegram_id = ?",
                    (telegram_id,),
                )
            ).fetchone()
        if row is None:
            raise ValueError("unknown user")
        return row["last_monitor_at"], row["last_summary_at"]

    async def mark_monitor_cycle(self, telegram_id: int, checked_at: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE user_settings SET last_monitor_at = ? WHERE telegram_id = ?",
                (checked_at, telegram_id),
            )
            await db.commit()

    async def mark_summary_sent(self, telegram_id: int, sent_at: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE user_settings SET last_summary_at = ? WHERE telegram_id = ?",
                (sent_at, telegram_id),
            )
            await db.commit()

    async def list_assets(self, *, active_only: bool = True) -> list[AssetCatalogEntry]:
        query = "SELECT * FROM asset_catalog"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY sort_order, symbol"
        async with self._connect() as db:
            rows = await (await db.execute(query)).fetchall()
        return [self._asset_from_row(row) for row in rows]

    async def upsert_asset(
        self,
        *,
        symbol: Asset,
        walt_code: str,
        exchange_code: str | None,
        active: bool,
        sort_order: int,
    ) -> None:
        entry = AssetCatalogEntry(symbol, walt_code, exchange_code, active, sort_order)
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO asset_catalog(symbol, walt_code, exchange_code, active, sort_order)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    walt_code = excluded.walt_code,
                    exchange_code = excluded.exchange_code,
                    active = excluded.active,
                    sort_order = excluded.sort_order
                """,
                (
                    entry.symbol.value,
                    entry.walt_code,
                    entry.exchange_code,
                    int(entry.active),
                    entry.sort_order,
                ),
            )
            await db.commit()

    async def set_asset_active(self, symbol: Asset, active: bool) -> None:
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE asset_catalog SET active = ? WHERE symbol = ?",
                (int(active), symbol.value),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise ValueError("unknown asset")
            await db.commit()

    async def get_user_assets(self, telegram_id: int) -> tuple[Asset, ...]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """
                    SELECT ua.symbol FROM user_assets ua
                    JOIN asset_catalog ac ON ac.symbol = ua.symbol
                    WHERE ua.telegram_id = ? ORDER BY ac.sort_order, ac.symbol
                    """,
                    (telegram_id,),
                )
            ).fetchall()
        return tuple(Asset(row[0]) for row in rows)

    async def replace_user_assets(self, telegram_id: int, assets: tuple[Asset, ...]) -> None:
        unique = tuple(dict.fromkeys(asset.value for asset in assets))
        if not unique:
            raise ValueError("at least one asset is required")
        async with self._connect() as db:
            placeholders = ",".join("?" for _ in unique)
            count = await self._scalar(
                db,
                f"SELECT COUNT(*) FROM asset_catalog "
                f"WHERE active = 1 AND symbol IN ({placeholders})",
                unique,
            )
            if count != len(unique):
                raise ValueError("unknown or inactive asset")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute("DELETE FROM user_assets WHERE telegram_id = ?", (telegram_id,))
                await db.executemany(
                    "INSERT INTO user_assets(telegram_id, symbol) VALUES (?, ?)",
                    [(telegram_id, symbol) for symbol in unique],
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def get_preferences(self, telegram_id: int) -> UserPreferences:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM user_preferences WHERE telegram_id = ?", (telegram_id,)
                )
            ).fetchone()
        if row is None:
            raise ValueError("unknown user")
        assets = await self.get_user_assets(telegram_id)
        return UserPreferences(
            assets=assets,
            directions=tuple(QuoteSide(value) for value in json.loads(row["directions"])),
            min_limit_from=Decimal(row["min_limit_from"]),
            min_limit_to=Decimal(row["min_limit_to"]) if row["min_limit_to"] else None,
            desired_amount=Decimal(row["desired_amount"]) if row["desired_amount"] else None,
            buy_payments=tuple(json.loads(row["buy_payments"])),
            sell_payments=tuple(json.loads(row["sell_payments"])),
            monitor_interval_seconds=row["monitor_interval_seconds"],
            summary_interval_minutes=row["summary_interval_minutes"],
            summary_enabled=bool(row["summary_enabled"]),
            timezone=row["timezone"],
            show_exchange_rate=bool(row["show_exchange_rate"]),
            notifications=NotificationPreferences(
                threshold=bool(row["notify_threshold"]),
                percent=bool(row["notify_percent"]),
                scheduled=bool(row["notify_scheduled"]),
                opportunity=bool(row["notify_opportunity"]),
            ),
            quiet_hours=QuietHours(
                enabled=bool(row["quiet_enabled"]),
                start=row["quiet_start"],
                end=row["quiet_end"],
            ),
        )

    async def update_preferences(self, telegram_id: int, **changes: object) -> None:
        columns = {
            "directions": lambda value: json.dumps(
                [item.value for item in value], separators=(",", ":")
            ),
            "min_limit_from": str,
            "min_limit_to": lambda value: str(value) if value is not None else None,
            "desired_amount": lambda value: str(value) if value is not None else None,
            "buy_payments": lambda value: json.dumps(list(value), separators=(",", ":")),
            "sell_payments": lambda value: json.dumps(list(value), separators=(",", ":")),
            "monitor_interval_seconds": int,
            "summary_interval_minutes": int,
            "summary_enabled": int,
            "timezone": str,
            "show_exchange_rate": int,
            "notify_threshold": int,
            "notify_percent": int,
            "notify_scheduled": int,
            "notify_opportunity": int,
            "quiet_enabled": int,
            "quiet_start": str,
            "quiet_end": str,
        }
        if not changes or any(name not in columns for name in changes):
            raise ValueError("unsupported preference field")
        async with self._connect() as db:
            if {"min_limit_from", "min_limit_to", "desired_amount"} & changes.keys():
                current = await (
                    await db.execute(
                        "SELECT min_limit_from, min_limit_to, desired_amount "
                        "FROM user_preferences WHERE telegram_id = ?",
                        (telegram_id,),
                    )
                ).fetchone()
                if current is None:
                    raise ValueError("unknown user")
                try:
                    lower = Decimal(
                        str(changes.get("min_limit_from", current["min_limit_from"]))
                    )
                    upper_value = changes.get("min_limit_to", current["min_limit_to"])
                    upper = (
                        None if upper_value is None else Decimal(str(upper_value))
                    )
                    amount_value = changes.get("desired_amount", current["desired_amount"])
                    amount = (
                        None if amount_value is None else Decimal(str(amount_value))
                    )
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError("Некорректное числовое значение настройки.") from exc
                if not lower.is_finite() or lower <= 0:
                    raise ValueError("Нижняя граница должна быть больше нуля.")
                if upper is not None and (not upper.is_finite() or upper <= 0):
                    raise ValueError("Верхняя граница должна быть больше нуля.")
                if upper is not None and lower > upper:
                    raise ValueError(
                        "Нижняя граница не может быть больше верхней."
                    )
                if amount is not None and (not amount.is_finite() or amount <= 0):
                    raise ValueError("Сумма сделки должна быть больше нуля.")
            assignments = ", ".join(f"{name} = ?" for name in changes)
            values = [columns[name](value) for name, value in changes.items()]
            values.append(telegram_id)
            cursor = await db.execute(
                f"UPDATE user_preferences SET {assignments} WHERE telegram_id = ?", values
            )
            if cursor.rowcount != 1:
                await db.rollback()
                raise ValueError("unknown user")
            await db.commit()

    async def reset_preferences(self, telegram_id: int) -> None:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "DELETE FROM user_preferences WHERE telegram_id = ?", (telegram_id,)
                )
                await db.execute(
                    "INSERT INTO user_preferences(telegram_id) VALUES (?)", (telegram_id,)
                )
                await db.execute("DELETE FROM user_assets WHERE telegram_id = ?", (telegram_id,))
                await db.execute(
                    "INSERT INTO user_assets(telegram_id, symbol) "
                    "SELECT ?, symbol FROM asset_catalog WHERE active = 1",
                    (telegram_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def save_ui_session(
        self, telegram_id: int, state: str, data: dict[str, object]
    ) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO ui_sessions(telegram_id, state, data_json) VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    state = excluded.state,
                    data_json = excluded.data_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_id, state, payload),
            )
            await db.commit()

    async def load_ui_session(
        self, telegram_id: int
    ) -> tuple[str, dict[str, object]] | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT state, data_json FROM ui_sessions WHERE telegram_id = ?",
                    (telegram_id,),
                )
            ).fetchone()
        return (row["state"], json.loads(row["data_json"])) if row else None

    async def clear_ui_session(self, telegram_id: int) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM ui_sessions WHERE telegram_id = ?", (telegram_id,))
            await db.commit()

    async def add_alert(
        self,
        telegram_id: int,
        *,
        kind: AlertKind,
        asset: Asset,
        quote_side: QuoteSide,
        target: str,
        threshold_direction: ThresholdDirection | None = None,
        baseline: str | None = None,
    ) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO alerts(
                    telegram_id, kind, asset, quote_side, threshold_direction, target, baseline
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_id,
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

    async def list_alerts(
        self, telegram_id: int, *, enabled_only: bool = False
    ) -> list[AlertRule]:
        query = "SELECT * FROM alerts WHERE telegram_id = ?"
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY id"
        async with self._connect() as db:
            rows = await (await db.execute(query, (telegram_id,))).fetchall()
        return [self._alert_from_row(row) for row in rows]

    async def delete_alert(self, telegram_id: int, alert_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM alerts WHERE telegram_id = ? AND id = ?", (telegram_id, alert_id)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def update_alert_state(
        self,
        telegram_id: int,
        alert_id: int,
        *,
        armed: bool | None = None,
        baseline: str | None = None,
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
        values.extend((telegram_id, alert_id))
        async with self._connect() as db:
            await db.execute(
                f"UPDATE alerts SET {', '.join(assignments)} "
                "WHERE telegram_id = ? AND id = ?",
                values,
            )
            await db.commit()

    async def add_schedule(
        self,
        telegram_id: int,
        *,
        kind: ScheduleKind,
        interval_hours: int | None = None,
        daily_time: str | None = None,
    ) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO schedules(telegram_id, kind, interval_hours, daily_time) "
                "VALUES (?, ?, ?, ?)",
                (telegram_id, kind.value, interval_hours, daily_time),
            )
            await db.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("failed to create schedule")
            return cursor.lastrowid

    async def list_schedules(
        self, telegram_id: int, *, enabled_only: bool = False
    ) -> list[ScheduleRule]:
        query = "SELECT * FROM schedules WHERE telegram_id = ?"
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY id"
        async with self._connect() as db:
            rows = await (await db.execute(query, (telegram_id,))).fetchall()
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

    async def mark_schedule_sent(
        self, telegram_id: int, schedule_id: int, sent_at: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE schedules SET last_sent_at = ? "
                "WHERE telegram_id = ? AND id = ?",
                (sent_at, telegram_id, schedule_id),
            )
            await db.commit()

    async def delete_schedule(self, telegram_id: int, schedule_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM schedules WHERE telegram_id = ? AND id = ?",
                (telegram_id, schedule_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_opportunity_settings(self, telegram_id: int) -> OpportunitySettings:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT enabled, mode, min_spread_percent, max_buy_premium_percent "
                    "FROM opportunity_settings WHERE telegram_id = ?",
                    (telegram_id,),
                )
            ).fetchone()
        if row is None:
            raise ValueError("unknown user")
        return OpportunitySettings(
            enabled=bool(row["enabled"]),
            mode=OpportunityMode(row["mode"]),
            min_spread_percent=Decimal(row["min_spread_percent"]),
            max_buy_premium_percent=Decimal(row["max_buy_premium_percent"]),
        )

    async def configure_opportunities(
        self,
        telegram_id: int,
        *,
        mode: OpportunityMode,
        min_spread_percent: Decimal,
        max_buy_premium_percent: Decimal | None = None,
    ) -> None:
        if (
            not min_spread_percent.is_finite()
            or min_spread_percent <= 0
            or min_spread_percent > Decimal("100")
        ):
            raise ValueError("min_spread_percent must be between 0 and 100")
        if max_buy_premium_percent is not None and (
            not max_buy_premium_percent.is_finite()
            or max_buy_premium_percent < 0
            or max_buy_premium_percent > Decimal("100")
        ):
            raise ValueError("max_buy_premium_percent must be between 0 and 100")
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE opportunity_settings SET enabled = 1, mode = ?, "
                "min_spread_percent = ?, max_buy_premium_percent = "
                "COALESCE(?, max_buy_premium_percent) WHERE telegram_id = ?",
                (
                    mode.value,
                    str(min_spread_percent),
                    str(max_buy_premium_percent)
                    if max_buy_premium_percent is not None
                    else None,
                    telegram_id,
                ),
            )
            await db.execute(
                "DELETE FROM opportunity_notifications WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.execute(
                "DELETE FROM opportunity_notification_history WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()

    async def disable_opportunities(self, telegram_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE opportunity_settings SET enabled = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()

    async def get_last_opportunity_ad(
        self, telegram_id: int, asset: Asset, side: QuoteSide
    ) -> str | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT last_ad_id FROM opportunity_notifications "
                    "WHERE telegram_id = ? AND asset = ? AND quote_side = ?",
                    (telegram_id, asset.value, side.value),
                )
            ).fetchone()
        return row[0] if row else None

    async def was_opportunity_notified(
        self,
        telegram_id: int,
        asset: Asset,
        side: QuoteSide,
        ad_id: str,
        now: datetime | None = None,
    ) -> bool:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM opportunity_notification_history WHERE expires_at <= ?",
                (timestamp.isoformat(),),
            )
            row = await (
                await db.execute(
                    "SELECT 1 FROM opportunity_notification_history "
                    "WHERE telegram_id = ? AND asset = ? AND quote_side = ? AND ad_id = ?",
                    (telegram_id, asset.value, side.value, ad_id),
                )
            ).fetchone()
            await db.commit()
        return row is not None

    async def mark_opportunity_notified(
        self, telegram_id: int, asset: Asset, side: QuoteSide, ad_id: str
    ) -> None:
        await self.mark_opportunities_notified(
            telegram_id, [(asset, side, ad_id, "")]
        )

    async def save_opportunity_history(
        self,
        telegram_id: int,
        asset: Asset,
        side: QuoteSide,
        ad_id: str,
        message_text: str,
        notified_at: datetime,
    ) -> None:
        timestamp = notified_at.astimezone(UTC)
        expires_at = timestamp + OPPORTUNITY_HISTORY_TTL
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM opportunity_notification_history WHERE expires_at <= ?",
                (timestamp.isoformat(),),
            )
            await db.execute(
                """
                INSERT INTO opportunity_notification_history(
                    telegram_id, asset, quote_side, ad_id,
                    notified_at, message_text, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id, asset, quote_side, ad_id) DO UPDATE SET
                    notified_at = excluded.notified_at,
                    message_text = excluded.message_text,
                    expires_at = excluded.expires_at
                """,
                (
                    telegram_id,
                    asset.value,
                    side.value,
                    ad_id,
                    timestamp.isoformat(),
                    message_text,
                    expires_at.isoformat(),
                ),
            )
            await db.commit()

    async def prune_opportunity_history(self, now: datetime | None = None) -> int:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM opportunity_notification_history WHERE expires_at <= ?",
                (timestamp.isoformat(),),
            )
            await db.commit()
            return max(cursor.rowcount, 0)

    async def list_opportunity_history(
        self, telegram_id: int, now: datetime | None = None
    ) -> list[OpportunityHistoryEntry]:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM opportunity_notification_history WHERE expires_at <= ?",
                (timestamp.isoformat(),),
            )
            rows = await (
                await db.execute(
                    """
                    SELECT rowid AS id, asset, quote_side, ad_id,
                           message_text, notified_at, expires_at
                    FROM opportunity_notification_history
                    WHERE telegram_id = ? AND message_text <> ''
                    ORDER BY notified_at DESC, rowid DESC
                    """,
                    (telegram_id,),
                )
            ).fetchall()
            await db.commit()
        return [self._opportunity_history_from_row(row) for row in rows]

    async def get_opportunity_history_entry(
        self, telegram_id: int, entry_id: int, now: datetime | None = None
    ) -> OpportunityHistoryEntry | None:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM opportunity_notification_history WHERE expires_at <= ?",
                (timestamp.isoformat(),),
            )
            row = await (
                await db.execute(
                    """
                    SELECT rowid AS id, asset, quote_side, ad_id,
                           message_text, notified_at, expires_at
                    FROM opportunity_notification_history
                    WHERE telegram_id = ? AND rowid = ? AND message_text <> ''
                    """,
                    (telegram_id, entry_id),
                )
            ).fetchone()
            await db.commit()
        return self._opportunity_history_from_row(row) if row else None

    async def mark_opportunities_notified(
        self,
        telegram_id: int,
        notifications: list[tuple[Asset, QuoteSide, str, str]],
        notified_at: datetime | None = None,
    ) -> None:
        if not notifications:
            return
        timestamp = (notified_at or datetime.now(UTC)).astimezone(UTC)
        expires_at = timestamp + OPPORTUNITY_HISTORY_TTL
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM opportunity_notification_history WHERE expires_at <= ?",
                (timestamp.isoformat(),),
            )
            await db.executemany(
                """
                INSERT INTO opportunity_notification_history(
                    telegram_id, asset, quote_side, ad_id,
                    notified_at, message_text, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id, asset, quote_side, ad_id) DO UPDATE SET
                    notified_at = excluded.notified_at,
                    message_text = excluded.message_text,
                    expires_at = excluded.expires_at
                """,
                [
                    (
                        telegram_id,
                        asset.value,
                        side.value,
                        ad_id,
                        timestamp.isoformat(),
                        message_text,
                        expires_at.isoformat(),
                    )
                    for asset, side, ad_id, message_text in notifications
                ],
            )
            await db.executemany(
                "INSERT INTO opportunity_notifications"
                "(telegram_id, asset, quote_side, last_ad_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(telegram_id, asset, quote_side) DO UPDATE SET "
                "last_ad_id = excluded.last_ad_id",
                [
                    (telegram_id, asset.value, side.value, ad_id)
                    for asset, side, ad_id, _ in notifications
                ],
            )
            await db.commit()

    @staticmethod
    async def _insert_user_defaults(db: aiosqlite.Connection, telegram_id: int) -> None:
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
            "INSERT OR IGNORE INTO user_assets(telegram_id, symbol) "
            "SELECT ?, symbol FROM asset_catalog WHERE active = 1 "
            "AND NOT EXISTS ("
            "SELECT 1 FROM user_assets WHERE telegram_id = ?)",
            (telegram_id, telegram_id),
        )

    @staticmethod
    async def _scalar(
        db: aiosqlite.Connection, query: str, params: tuple[object, ...] = ()
    ) -> object | None:
        row = await (await db.execute(query, params)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _user_from_row(row: aiosqlite.Row) -> User:
        return User(
            telegram_id=row["telegram_id"],
            phone=row["phone"],
            role=UserRole(row["role"]),
            status=AccessStatus(row["status"]),
            first_name=row["first_name"],
            username=row["username"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _asset_from_row(row: aiosqlite.Row) -> AssetCatalogEntry:
        return AssetCatalogEntry(
            symbol=Asset(row["symbol"]),
            walt_code=row["walt_code"],
            exchange_code=row["exchange_code"],
            active=bool(row["active"]),
            sort_order=row["sort_order"],
        )

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

    @staticmethod
    def _opportunity_history_from_row(row: aiosqlite.Row) -> OpportunityHistoryEntry:
        return OpportunityHistoryEntry(
            id=int(row["id"]),
            asset=Asset(str(row["asset"])),
            quote_side=QuoteSide(str(row["quote_side"])),
            ad_id=str(row["ad_id"]),
            message_text=str(row["message_text"]),
            notified_at=datetime.fromisoformat(str(row["notified_at"])),
            expires_at=datetime.fromisoformat(str(row["expires_at"])),
        )
