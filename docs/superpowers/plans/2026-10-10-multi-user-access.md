# Multi-user Walt P2P Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить текущий однопользовательский бот в закрытый многопользовательский сервис с администрированием доступа, отдельными Walt-токенами, персональными настройками и полным каталогом P2P-монет Walt.

**Architecture:** SQLite остаётся единым хранилищем, но все торговые сущности получают `telegram_id`, а секреты сохраняются только в зашифрованном виде. Небольшие отдельные роутеры отвечают за регистрацию, администрирование и настройки; фоновый координатор создаёт изолированный Walt-клиент для каждого активного пользователя и ограничивает параллелизм семафором. Динамический каталог отделяет отображаемый тикер от кодов Walt и источников справочного курса.

**Tech Stack:** Python 3.12, aiogram 3.31, aiohttp, aiosqlite, Pydantic Settings, `cryptography.fernet`, SQLite, pytest/pytest-asyncio, Ruff, systemd.

**Spec:** `docs/superpowers/specs/2026-10-10-multi-user-access-design.md`

## Global Constraints

- Неизменяемый администратор: Telegram ID `768929900`.
- Роли: `ADMIN`, `USER`, `PREMIUM`; состояния: `PENDING`, `AWAITING_TOKEN`, `ACTIVE`, `REJECTED`, `REVOKED`.
- Первоначальный каталог Walt P2P: `GRAM`, `USDT`, `BTC`, `NOT`, `ETH`; `GRAM` никогда не показывается как `TON`, `BTC` — как «Биткоин».
- Все пользовательские данные, правила, настройки, история и секреты изолированы по `telegram_id`.
- Walt-токены шифруются Fernet-ключом `CREDENTIAL_ENCRYPTION_KEY`; открытые токены и телефоны не попадают в журнал.
- Интерфейс полностью русский, работает преимущественно inline-кнопками, редактирует одно актуальное сообщение и удаляет чувствительный пользовательский ввод.
- Главное меню состоит из «Текущие цены», «Уведомления», «Настройки» и доступного администратору раздела «Пользователи»; однотипные операции находятся только внутри своего раздела.
- Письменный список `/команд` существует только в системном меню Telegram и не дублируется в сообщениях; отдельной кнопки «Помощь» нет.
- Обычные изменения сохраняются сразу; критические действия требуют подтверждения; отмена не меняет сохранённое значение.
- Тесты запускаются точечно после каждого изменения; полный набор запускается один раз перед выпуском.
- Автоматическая торговля и операции с кошельком не добавляются.

## Review Focus

- Одновременные повторные нажатия админских кнопок должны дать одно допустимое изменение состояния, а не дубликат или повреждение заявки.
- Повреждённый шифротекст либо неправильный мастер-ключ должен заблокировать только одного пользователя и не раскрыть секрет в журнале.
- Тихий период через полночь в пользовательском часовом поясе не должен ни отправить, ни навсегда поглотить актуальное уведомление.
- Устаревший callback для удалённого пользователя, монеты или страницы должен безопасно вернуть актуальный экран.
- Новая активная монета в каталоге без биржевого курса должна показывать Walt P2P, но не создавать ложное сравнительное уведомление.

---

### Task 1: Dynamic domain model and asset catalog

**Files:**
- Modify: `src/walt_price_bot/models.py`
- Create: `tests/test_assets.py`
- Modify: `tests/test_market.py`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Produces: `Asset(symbol: str)`, `AssetCatalogEntry(symbol, walt_code, exchange_code, active, sort_order)`, constants `GRAM`, `USDT`, `BTC`, `NOT`, `ETH`.
- Produces: `UserRole`, `AccessStatus`, `User`, `UserPreferences`, `NotificationPreferences`, `QuietHours`.
- Preserves: `.value` as the display ticker during migration so existing formatting code can be adapted incrementally.

- [ ] **Step 1: Write focused failing tests** for ticker normalization, equality, arbitrary catalog symbols, the five seed assets, GRAM display/Walt-code separation, and rejection of malformed ticker strings.
- [ ] **Step 2: Run `pytest tests/test_assets.py tests/test_market.py tests/test_ui.py -q`** and confirm failures are limited to the missing dynamic model and changed ticker expectations.
- [ ] **Step 3: Replace the closed `StrEnum` with an immutable validated value object** and add the access/preferences dataclasses and enums listed in Interfaces.
- [ ] **Step 4: Adapt only directly affected comparisons from identity to equality** and change UI expectations from `TON`/«Биткоин» to `GRAM`/`BTC`.
- [ ] **Step 5: Re-run the same focused tests** and require PASS.
- [ ] **Step 6: Commit** with `feat: add dynamic asset domain model`.

### Task 2: Transactional schema migration and tenant-aware repository

**Files:**
- Create: `src/walt_price_bot/schema.py`
- Modify: `src/walt_price_bot/repository.py`
- Create: `tests/test_repository_users.py`
- Create: `tests/test_repository_migration.py`
- Modify: `tests/test_repository.py`

**Interfaces:**
- Produces: `Repository.get_user(telegram_id) -> User | None`, `create_or_refresh_request(...) -> User`, `transition_user(...) -> User`, `list_users(status=None, limit=10, offset=0)`, `delete_user(...)`.
- Produces: `get_preferences`, `update_preferences`, `reset_preferences`, `list_assets`, `replace_user_assets`, `save_ui_session`, `load_ui_session`.
- Changes existing alert/schedule/opportunity methods to require `telegram_id` as their first domain argument.

- [ ] **Step 1: Write migration tests** that build a real 1.4.0 database, initialize twice, verify admin `768929900`, record counts, `TON`→`GRAM`, foreign keys, ciphertext column, and rollback on injected copy failure.
- [ ] **Step 2: Write tenant repository tests** proving duplicate phone rejection, valid/invalid state transitions, per-user isolation, cascade deletion, immutable admin, pagination, catalog activation, and stale-entity lookup.
- [ ] **Step 3: Run only `tests/test_repository.py`, `tests/test_repository_users.py`, and `tests/test_repository_migration.py`** and confirm expected failures.
- [ ] **Step 4: Move schema/version logic into `schema.py`** with one `BEGIN IMMEDIATE` migration transaction, explicit row-count checks, seed assets, schema version, and idempotency.
- [ ] **Step 5: Implement repository interfaces** with parameterized SQL, foreign keys enabled on every connection, atomic compare-and-transition updates, and per-user locks keyed by `telegram_id`.
- [ ] **Step 6: Re-run the three repository test files** and require PASS.
- [ ] **Step 7: Commit** with `feat: add tenant database and migration`.

### Task 3: Credential encryption, configuration, and safe installation

**Files:**
- Create: `src/walt_price_bot/credentials.py`
- Modify: `src/walt_price_bot/config.py`
- Modify: `pyproject.toml`
- Modify: `requirements.lock`
- Modify: `uv.lock`
- Modify: `.env.example`
- Modify: `deploy/install.sh`
- Create: `tests/test_credentials.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_deploy_assets.py`

**Interfaces:**
- Produces: `CredentialCipher(key: SecretStr)`, `encrypt(token: str) -> bytes`, `decrypt(ciphertext: bytes) -> SecretStr`.
- Produces: required `Settings.credential_encryption_key`; keeps optional legacy `walt_api_key` only for admin migration.

- [ ] **Step 1: Add failing tests** for Fernet round-trip, randomized ciphertext, invalid key startup failure, invalid ciphertext isolation, redacted `repr`, and installer idempotently creating a mode-0600 key without printing it.
- [ ] **Step 2: Run only credential/config/deploy tests** and confirm failure.
- [ ] **Step 3: Add `cryptography` through the project dependency resolver** and regenerate both lock files without hand-editing hashes.
- [ ] **Step 4: Implement `CredentialCipher`** so exceptions never include token, key, or ciphertext.
- [ ] **Step 5: Update settings and installer** to generate a URL-safe 32-byte key only when absent and preserve existing env values.
- [ ] **Step 6: Re-run the focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: encrypt per-user Walt credentials`.

### Task 4: Persistent aiogram state and centralized authorization

**Files:**
- Create: `src/walt_price_bot/fsm_storage.py`
- Create: `src/walt_price_bot/access.py`
- Create: `tests/test_fsm_storage.py`
- Create: `tests/test_access.py`
- Modify: `src/walt_price_bot/main.py`

**Interfaces:**
- Produces: `SQLiteFSMStorage(BaseStorage)` implementing `get_state`, `set_state`, `get_data`, `set_data`, `update_data`, `close`.
- Produces: `AccessMiddleware(repository, admin_id)` that attaches `event_user` and rejects protected messages/callbacks server-side.
- Produces: `AccessRequirement` values `PUBLIC`, `ACTIVE`, `ADMIN` and router flags consumed by the middleware.

- [ ] **Step 1: Write failing tests** for state/data survival across storage instances, cancel preserving previous values, unknown/PENDING/AWAITING_TOKEN/REJECTED/REVOKED responses, callback denial, and hard-coded admin invariants.
- [ ] **Step 2: Run the two new test files** and confirm failure.
- [ ] **Step 3: Implement SQLite FSM storage** using the repository database and atomic JSON updates.
- [ ] **Step 4: Implement centralized access middleware** with typed flags and generic non-leaking Russian status responses.
- [ ] **Step 5: Wire persistent storage and middleware into `Dispatcher`** without changing business handlers yet.
- [ ] **Step 6: Re-run focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: persist bot state and enforce access`.

### Task 5: Registration, approval handoff, and user token activation

**Files:**
- Create: `src/walt_price_bot/onboarding.py`
- Create: `src/walt_price_bot/keyboards.py`
- Modify: `src/walt_price_bot/walt.py`
- Create: `tests/test_onboarding.py`
- Modify: `tests/test_walt.py`

**Interfaces:**
- Produces: `normalize_phone(contact: Contact, sender_id: int) -> str`.
- Produces: `validate_walt_token(session, token: SecretStr, probe_asset: AssetCatalogEntry) -> TokenValidationResult`.
- Produces: `build_onboarding_router(repository, cipher, session, admin_id) -> Router`.

- [ ] **Step 1: Write failing tests** for native self-contact only, normalized unique phone, idempotent repeated `/start`, one admin notification, approval to `AWAITING_TOKEN`, 401/403 rejection, transient retry, successful encrypted activation, token-message deletion, and legacy admin-token migration.
- [ ] **Step 2: Run onboarding and Walt tests** and confirm failure.
- [ ] **Step 3: Implement contact onboarding and reply-keyboard cleanup** so the phone keyboard disappears immediately after receipt.
- [ ] **Step 4: Add a bounded token validation method to `WaltClient`** with typed auth/transient results and redacted errors.
- [ ] **Step 5: Implement token entry/replacement flow** where a failed replacement never overwrites the old ciphertext.
- [ ] **Step 6: Re-run focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: add secure user onboarding`.

### Task 6: Administrator panel

**Files:**
- Create: `src/walt_price_bot/admin.py`
- Modify: `src/walt_price_bot/keyboards.py`
- Create: `tests/test_admin.py`

**Interfaces:**
- Produces: `build_admin_router(repository, admin_id) -> Router`.
- Callback payloads use typed aiogram `CallbackData` classes carrying action, target ID, expected status/version, and page.

- [ ] **Step 1: Write failing tests** for lists/pages/details, approve/reject/revoke/restore, USER/PREMIUM role changes, delete confirmation/cascade, immutable admin, unauthorized forged callbacks, duplicate clicks, and stale callbacks.
- [ ] **Step 2: Run `pytest tests/test_admin.py -q`** and confirm failure.
- [ ] **Step 3: Implement compact Russian admin screens and typed callbacks** using atomic repository transitions and one-message replacement.
- [ ] **Step 4: Ensure admin decisions notify the affected user only after commit** and delivery failure does not roll back the access decision.
- [ ] **Step 5: Re-run the admin tests** and require PASS.
- [ ] **Step 6: Commit** with `feat: add access administration panel`.

### Task 7: Persistent personal settings UI

**Files:**
- Create: `src/walt_price_bot/preferences.py`
- Modify: `src/walt_price_bot/keyboards.py`
- Modify: `src/walt_price_bot/ui.py`
- Create: `tests/test_preferences.py`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Produces: `build_preferences_router(repository) -> Router`.
- Produces: `format_preferences(preferences, assets) -> str`, `parse_money(text) -> Decimal`, `summary_interval_minutes(hours, minutes) -> int`.
- Uses repository atomic field updates and persistent FSM sessions from Tasks 2 and 4.

- [ ] **Step 1: Write failing tests** for one-click toggles, current-value rendering, assets/search/pagination/select-all, non-empty asset validation, directions, payment filters, 500/1000/2000/unlimited/custom limits, desired amount, intervals 1 minute–24 hours, timezone, Coinbase display, alert categories, quiet hours through midnight, pause, reset, back/home/cancel, and restart restoration.
- [ ] **Step 2: Run only preference and UI tests** and confirm failure.
- [ ] **Step 3: Implement typed setting callbacks and immediate atomic saves**; callback answers are short and no extra chat message is created.
- [ ] **Step 4: Implement manual-value states** with validation, input deletion, cancellation, and no overwrite on error.
- [ ] **Step 5: Implement reset confirmation** preserving role, access, credentials, rules, schedules, and saved summary interval when only disabled.
- [ ] **Step 6: Re-run focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: add persistent personal settings`.

### Task 8: Dynamic Walt queries, exchange references, and user filters

**Files:**
- Modify: `src/walt_price_bot/walt.py`
- Modify: `src/walt_price_bot/exchange.py`
- Modify: `src/walt_price_bot/market.py`
- Modify: `src/walt_price_bot/runtime.py`
- Create: `tests/test_user_market.py`
- Modify: `tests/test_exchange.py`
- Modify: `tests/test_walt.py`

**Interfaces:**
- Changes: `WaltClient.fetch_ads(asset: AssetCatalogEntry, side: QuoteSide, filters: MarketFilters) -> list[P2PAd]`.
- Changes: `MarketService.get_snapshots(assets: Sequence[AssetCatalogEntry], filters: MarketFilters) -> list[MarketSnapshot]`.
- Produces: `MarketFilters(directions, min_limit_from, min_limit_to, desired_amount, buy_payments, sell_payments)`.

- [ ] **Step 1: Write failing tests** for all five seed assets, GRAM provider-code mapping, dynamic added asset, user-selected subset, amount coverage, payment/direction filters, unavailable exchange rate, and malformed provider asset codes.
- [ ] **Step 2: Run only Walt/exchange/user-market tests** and confirm failure.
- [ ] **Step 3: Remove iteration over the old enum** and drive Walt/exchange requests from repository catalog entries and user preferences.
- [ ] **Step 4: Apply filters before best-offer selection** while retaining the existing online, RUB, pagination, and payment behavior.
- [ ] **Step 5: Keep Walt results when exchange reference is unavailable** but suppress comparative opportunity calculations for that asset.
- [ ] **Step 6: Re-run focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: monitor user-selected Walt assets`.

### Task 9: Multi-user monitor, summaries, quiet hours, and deduplication

**Files:**
- Create: `src/walt_price_bot/monitoring.py`
- Modify: `src/walt_price_bot/runtime.py`
- Modify: `src/walt_price_bot/scheduling.py`
- Modify: `src/walt_price_bot/messaging.py`
- Create: `tests/test_multi_user_monitoring.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_scheduling.py`

**Interfaces:**
- Produces: `UserMarketProviderFactory.for_user(user, preferences) -> SnapshotProvider`.
- Produces: `run_user_cycle(user, ...)` and `run_multi_user_cycle(...)` with a bounded `asyncio.Semaphore`.
- Produces: `interval_summary_is_due`, `in_quiet_hours`, and per-category delivery guards.

- [ ] **Step 1: Write failing tests** for two distinct decrypted tokens, tenant rule/history isolation, one-user failure isolation, revoke during a cycle, per-user intervals, 1-minute summary, restart persistence, no duplicate scheduler, quiet hours across midnight, later delivery of still-current events, category toggles, cooldown, send-before-mark semantics, and corrupted credential isolation.
- [ ] **Step 2: Run only multi-user runtime/scheduling tests** and confirm failure.
- [ ] **Step 3: Implement per-user provider factory and bounded coordinator** with shared aiohttp session and shared cached reference quotes where codes match.
- [ ] **Step 4: Move single-user alert/opportunity/schedule evaluation into `run_user_cycle`** and include `telegram_id` in every repository operation.
- [ ] **Step 5: Implement summary interval, quiet hours, category gating, cooldown, and delivery marking only after successful send**.
- [ ] **Step 6: Re-run focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: isolate multi-user market monitoring`.

### Task 10: Adapt existing commands and menus to tenants and dynamic assets

**Files:**
- Modify: `src/walt_price_bot/handlers.py`
- Modify: `src/walt_price_bot/ui.py`
- Modify: `src/walt_price_bot/main.py`
- Modify: `tests/test_handlers.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_messaging.py`

**Interfaces:**
- Existing commands remain: `/start`, `/price`, `/deals`, `/alert`, `/change`, `/schedule`, `/alerts`, `/delete`, `/pause`, `/resume`, `/help`.
- New commands: `/token`, `/settings`, `/admin`.
- `main_menu_keyboard(role, paused)` conditionally includes settings, token, and admin controls.
- `notifications_menu_keyboard(paused)` содержит правила, выгодные лоты, расписания и один контекстный переключатель паузы; удаление открывается из карточки правила.

- [ ] **Step 1: Update handler tests** to prove every existing command uses the sender's tenant ID and selected asset catalog, forged callbacks cannot cross tenants, and menu/profile texts are fully Russian and use current tickers. Assert that only «Текущие цены» preserves its old direct button, notification actions are grouped, delete is rule-local, pause/resume is one contextual toggle, admin controls are role-gated, and no bot message or button duplicates the `/command` list.
- [ ] **Step 2: Run handler/main/messaging tests** and confirm failure.
- [ ] **Step 3: Split router construction into onboarding/admin/preferences/existing-feature routers** while keeping `build_router` as the composition entry point.
- [ ] **Step 4: Pass authenticated `telegram_id` through all existing rule, schedule, pause, price, and opportunity paths** and replace static asset keyboards with catalog-backed typed callbacks.
- [ ] **Step 5: Update `/start`, the Telegram system command menu, BotFather descriptions, and hierarchical button menus** using the exact approved copy from the spec; remove the visible help button and command-list messages while preserving one-message replacement and incoming-message deletion.
- [ ] **Step 6: Re-run focused tests** and require PASS.
- [ ] **Step 7: Commit** with `feat: expose tenant-aware Russian bot interface`.

### Task 11: Release hardening, migration rehearsal, and documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `src/walt_price_bot/__init__.py`
- Modify: `deploy/walt-price-bot.service`
- Modify: `deploy/install.sh`
- Modify: `tests/test_deploy_assets.py`

**Interfaces:**
- Release version: next minor version after `1.4.0`.
- Installer accepts the existing source-directory argument and remains idempotent.

- [ ] **Step 1: Add deploy tests** for backup-before-migration, LF shell assets, env preservation, encryption-key generation, mode 0600, service restart, and failure leaving the old database recoverable.
- [ ] **Step 2: Run only deploy tests** and confirm any missing behavior.
- [ ] **Step 3: Update installer/service, version, changelog, and operator documentation** without adding secrets or real server values.
- [ ] **Step 4: Re-run deploy tests** and require PASS.
- [ ] **Step 5: Commit** with `release: prepare multi-user Walt monitor`.

### Task 12: Final verification, GitHub publication, and server deployment

**Files:**
- No product files unless verification exposes a defect; any defect receives its own focused failing test and fix commit.

**Interfaces:**
- Consumes the configured Git remote and server connection data already present outside tracked files.
- Produces a pushed public GitHub `main` and a healthy running systemd service using the migrated production database.

- [ ] **Step 1: Run the full suite once:** `pytest -q`; expected result: all tests pass, with only explicitly marked integration skips.
- [ ] **Step 2: Run `ruff check .`, `python -m compileall -q src`, and `git diff --check`**; require zero errors.
- [ ] **Step 3: Scan tracked files and Git history introduced by this release** for Telegram tokens, Walt keys, Fernet keys, phone numbers, private server credentials, `.env`, and database files; require no matches beyond placeholders/tests.
- [ ] **Step 4: Rehearse migration twice against a disposable copy of a representative 1.4.0 database** and compare entity counts, admin ownership, catalog mappings, and encrypted credential presence.
- [ ] **Step 5: Push commits to the existing public GitHub repository** and verify the remote commit equals local `HEAD`.
- [ ] **Step 6: Deploy with the existing installer to the configured server**, allowing its recoverable database backup, then verify `systemctl is-active`, recent sanitized journal output, schema version, admin ID, encrypted credential row, and no plaintext token/phone leakage.
- [ ] **Step 7: Perform a narrow Telegram smoke check** for `/start`, main menu, settings, admin panel, current prices, and one non-destructive setting toggle; restore the setting after the check.
- [ ] **Step 8: Record the deployed version and verification evidence in the final handoff** without exposing secrets.

