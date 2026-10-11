# Multi-Opportunity Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Показывать несколько выгодных Walt-лотов, надёжно повторять их через 10 минут и не терять одновременные Telegram-события.

**Architecture:** Расширить снимок рынка отфильтрованными списками, сохранив лучшие `buy`/`sell`. Собирать события цикла до одной отправки, а историю перевести на неповторяемые ID и пагинацию.

**Tech Stack:** Python 3.12+, aiogram, aiohttp, aiosqlite, pytest, Ruff, Bash/systemd.

**Spec:** `docs/superpowers/specs/2026-10-11-multi-opportunity-reliability-design.md`

## Global Constraints

- Не менять формулы выгодности, названия кнопок, роли, admin ID и источники Walt/Coinbase.
- Не создавать новые Walt-запросы для подбора списка.
- Не отмечать событие доставленным до успешной Telegram-отправки.
- Видимая история и блокировка повтора действуют 10 минут.
- Производственное развёртывание не выполнять.

## Review Focus

- 150 объявлений в одной группе: сортировка, дедупликация и длина Telegram-сообщения.
- Повтор ровно на границе 10 минут и после перезапуска.
- Удаление старой строки и устаревший callback: чужая или новая запись не открывается.
- Сбой Telegram или SQLite между отправкой и фиксацией: нет бесконтрольного спама или ложной доставки.
- Сбой установки после остановки: возвращаются последние данные, код и состояние systemd.

---

### Task 1: Отфильтрованные списки рынка

**Files:** `src/walt_price_bot/market.py`, `src/walt_price_bot/models.py`, `src/walt_price_bot/runtime.py`, `tests/test_market.py`, `tests/test_runtime.py`

**Interfaces:** Produces `select_buy_offers(...) -> tuple[P2PAd, ...]`, `select_sell_offers(...) -> tuple[P2PAd, ...]` and compatible `MarketSnapshot.buy_offers`/`sell_offers`.

- [x] Write tests proving all eligible ads survive, invalid/offline/duplicate ads do not, and buy/sell ordering is correct.
- [x] Run the new tests and verify RED.
- [x] Implement the selectors and populate snapshots from the same Walt responses.
- [x] Run market/runtime tests and verify GREEN.

### Task 2: Стабильная история и 10-минутный учёт

**Files:** `src/walt_price_bot/schema.py`, `src/walt_price_bot/repository.py`, `tests/test_repository.py`, `tests/test_repository_migration.py`

**Interfaces:** Produces schema v6, stable history IDs, paginated queries and one-query recent-key lookup.

- [x] Write migration, non-reuse, boundary, pagination, isolation and restart tests; verify RED.
- [x] Implement the transactional v5→v6 rebuild and repository API.
- [x] Run repository/migration tests and verify GREEN.

### Task 3: Подборка лотов и атомарная доставка событий

**Files:** `src/walt_price_bot/runtime.py`, `src/walt_price_bot/ui.py`, `tests/test_runtime.py`, `tests/test_ui.py`

**Interfaces:** Consumes Task 1 candidate tuples and Task 2 recent keys; produces one bounded digest and post-send state mutations.

- [x] Write tests for multiple ads, top-five display, new ads during cooldown, 10-minute repeat, simultaneous alert kinds and failed delivery; verify RED.
- [x] Implement per-group selection, bounded digest composition and commit-after-send behavior.
- [x] Run runtime/UI tests and verify GREEN.

### Task 4: Пагинация истории и безопасные callback

**Files:** `src/walt_price_bot/handlers.py`, `src/walt_price_bot/ui.py`, `tests/test_handlers.py`, `tests/test_ui.py`

**Interfaces:** Consumes stable IDs and paginated repository calls; produces page/open callbacks preserving the source page.

- [x] Write tests for 50 entries, page clamps, back navigation, expired/foreign/stale IDs; verify RED.
- [x] Implement 15-entry pages and safe navigation without renaming visible sections.
- [x] Run handler/UI tests and verify GREEN.

### Task 5: Глобальная очистка и безопасный установщик

**Files:** `src/walt_price_bot/monitoring.py`, `deploy/install.sh`, `tests/test_multi_user_monitoring.py`, `tests/test_deploy_assets.py`

**Interfaces:** Produces once-per-minute cleanup and a stop→final-backup→migration-check→bounded-health-check install sequence.

- [x] Write scheduler and executable installer-harness tests; verify RED.
- [x] Implement cleanup cadence, move final backup after stop, validate migrated SQLite and observe systemd stability.
- [x] Run monitoring/deploy tests and `bash -n`; verify GREEN.

### Task 6: Релиз и полная проверка

**Files:** `README.md`, `CHANGELOG.md`, `pyproject.toml`, `src/walt_price_bot/__init__.py`, `uv.lock`

**Interfaces:** Documents and packages the verified release without deploying production.

- [x] Update only factual behavior and deployment/rollback instructions; bump the minor version.
- [x] Run `uv sync --all-extras --frozen`, `pytest -m "not integration"`, Ruff, compileall, Bash syntax, migration/integrity and secret scans.
- [x] Review the complete diff against commit `796de2c1816677dbfeb8adf3f80cb027dade4fc5` and publish only after all checks pass.
