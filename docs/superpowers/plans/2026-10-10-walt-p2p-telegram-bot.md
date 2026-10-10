# Walt P2P Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, test, and deploy an owner-only Telegram bot that monitors Walt P2P prices for USDT/RUB and TON/RUB and sends configurable alerts.

**Architecture:** An asynchronous Python application separates Walt API access, market selection, alert evaluation, SQLite persistence, Telegram handlers, and the monitoring loop. One shared polling cycle fetches four market snapshots every 30 seconds and evaluates every enabled rule without exposing secrets.

**Tech Stack:** Python 3.12, aiogram 3, aiohttp, aiosqlite, pydantic-settings, pytest, pytest-asyncio, Ruff, systemd.

**Spec:** `docs/superpowers/specs/2026-10-10-walt-p2p-telegram-bot-design.md`

## Global Constraints

- Monitor only `USDT/RUB` and `TON/RUB`; never execute trades.
- Poll Walt every 30 seconds and use `X-API-Key` without logging it.
- Use `Asia/Yekaterinburg` for daily schedules.
- Restrict the bot to one owner and persist rules in SQLite.
- Store deployment secrets only in `/etc/walt-price-bot.env` with mode `0600`.
- Deploy under a non-login system user with a hardened, auto-restarting systemd service.

## Review Focus

- Empty or malformed Walt responses must not become zero-price alerts; covered by Task 2.
- Decimal comma, zero, negative, and excessive percentage input must be rejected or normalized predictably; covered by Task 4.
- Thresholds must notify once per crossing and rearm only after returning across the boundary; covered by Task 3.
- A failed Telegram send must leave alert state eligible for retry; covered by Task 5.
- Concurrent first `/start` requests must not claim two owners; covered by Task 1.

---

### Task 1: Project foundation and persistent repository

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/walt_price_bot/config.py`, `src/walt_price_bot/models.py`, `src/walt_price_bot/repository.py`
- Test: `tests/test_repository.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `Settings`, `AlertRule`, `ScheduleRule`, and async `Repository` methods for owner, pause state, alerts, schedules, and delivery state.

- [ ] Write failing configuration and repository tests, including atomic owner claim.
- [ ] Run focused tests and verify the expected import failures.
- [ ] Implement the minimum configuration, schema migration, and repository API.
- [ ] Run focused tests and the full suite.
- [ ] Commit as `feat: add configuration and persistent storage`.

### Task 2: Walt client and market selection

**Files:**
- Create: `src/walt_price_bot/walt.py`, `src/walt_price_bot/market.py`
- Test: `tests/test_walt.py`, `tests/test_market.py`

**Interfaces:**
- Consumes: domain models from Task 1.
- Produces: `WaltClient.fetch_ads(asset, side)`, `select_best_buy_offer`, `select_best_sell_offer`, and `MarketSnapshot`.

- [ ] Write failing tests for payloads, response validation, retries, empty lists, and best-price selection.
- [ ] Run focused tests and verify failures are caused by missing behavior.
- [ ] Implement strict response parsing, bounded retry handling, and Decimal-based selection.
- [ ] Run focused tests and the full suite.
- [ ] Commit as `feat: add Walt market data client`.

### Task 3: Alert and scheduling engine

**Files:**
- Create: `src/walt_price_bot/alerts.py`, `src/walt_price_bot/scheduling.py`
- Test: `tests/test_alerts.py`, `tests/test_scheduling.py`

**Interfaces:**
- Consumes: rule and market models from Tasks 1-2.
- Produces: pure `evaluate_threshold`, `evaluate_percent_change`, `next_schedule_due`, and state transitions applied only after successful delivery.

- [ ] Write failing tests for threshold crossing/rearming, positive and negative percentage movement, and Yekaterinburg schedules.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement pure evaluation functions using Decimal and timezone-aware datetimes.
- [ ] Run focused tests and the full suite.
- [ ] Commit as `feat: add notification rule engine`.

### Task 4: Telegram interface

**Files:**
- Create: `src/walt_price_bot/ui.py`, `src/walt_price_bot/handlers.py`
- Test: `tests/test_ui.py`, `tests/test_handlers.py`

**Interfaces:**
- Consumes: repository, market snapshots, and rule constructors.
- Produces: routers and FSM flows for `/start`, `/price`, `/alert`, `/change`, `/schedule`, `/alerts`, `/delete`, `/pause`, `/resume`, and `/help`.

- [ ] Write failing tests for owner-only access, menu text, rule input validation, and command behavior.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement Russian-language handlers and inline keyboards.
- [ ] Run focused tests and the full suite.
- [ ] Commit as `feat: add Telegram bot interface`.

### Task 5: Monitoring runtime and delivery semantics

**Files:**
- Create: `src/walt_price_bot/runtime.py`, `src/walt_price_bot/main.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: all earlier components.
- Produces: `run_monitor_cycle`, background monitor task, graceful startup/shutdown, and the executable entry point.

- [ ] Write failing tests proving shared polling, paused behavior, successful state commits, failed-send retry eligibility, and error isolation.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement the orchestration and structured secret-free logging.
- [ ] Run focused tests and the full suite.
- [ ] Commit as `feat: integrate Walt monitoring runtime`.

### Task 6: Operations, verification, and deployment

**Files:**
- Create: `README.md`, `deploy/walt-price-bot.service`, `deploy/install.sh`, `CHANGELOG.md`
- Test: `tests/test_deploy_assets.py`, `tests/integration/test_walt_live.py`

**Interfaces:**
- Consumes: application entry point and environment contract.
- Produces: reproducible install assets and production service `walt-price-bot`.

- [ ] Write failing tests for deployment asset hardening and an opt-in live Walt probe.
- [ ] Implement systemd unit, installer, operator documentation, and release notes.
- [ ] Run pytest, Ruff, compile checks, and the real Walt probe.
- [ ] Inspect the complete diff for secrets, then commit as `chore: add production deployment`.
- [ ] Copy the verified source to the server, install dependencies, create the protected environment file, start the service, and verify `systemctl`, logs, Telegram API connectivity, and persisted restart behavior.
