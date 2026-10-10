# Walt P2P Monitor

Telegram-бот для мониторинга лучших P2P-цен `USDT/RUB` и `TON/RUB` в Walt. Бот показывает цены покупки и продажи, отслеживает пороги и процентные изменения, а также отправляет сводки по расписанию. Сделки не совершаются.

## Команды

- `/price` — текущие лучшие цены;
- `/alert` — ценовой порог;
- `/change` — уведомление по процентному изменению;
- `/schedule` — периодическая или ежедневная сводка;
- `/alerts` и `/delete` — просмотр и удаление правил;
- `/pause` и `/resume` — пауза и возобновление уведомлений.

Первый `/start` закрепляет бота за владельцем, если `OWNER_TELEGRAM_ID` не задан заранее.

## Локальная разработка

```powershell
uv sync --all-extras --frozen
Copy-Item .env.example .env
# Заполните .env, затем:
uv run pytest
uv run ruff check src tests
uv run python -m walt_price_bot.main
```

## Сервер

Переменные хранятся в `/etc/walt-price-bot.env`, а SQLite — в `/var/lib/walt-price-bot/walt-price-bot.db`. Код устанавливается в `/opt/walt-price-bot` и запускается службой `walt-price-bot.service`.

Проверка:

```bash
systemctl status walt-price-bot --no-pager
journalctl -u walt-price-bot -n 100 --no-pager
```

## Источники

- https://help.ru.walt.io/article/933-p2p-api-ru
- https://docs.walt.io/p2p
- https://docs.aiogram.dev/en/latest/
- https://docs.aiohttp.org/en/stable/client_quickstart.html
