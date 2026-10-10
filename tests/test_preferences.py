from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from walt_price_bot.models import BTC, ETH, QuoteSide
from walt_price_bot.preferences import (
    SettingsAction,
    SettingsCallback,
    format_preferences,
    limit_settings_keyboard,
    notification_categories_keyboard,
    parse_money,
    payment_methods_keyboard,
    return_to_main_menu,
    settings_keyboard,
    show_quiet_hours,
    summary_interval_minutes,
    time_picker_keyboard,
    toggle_asset,
    toggle_payment_method,
)
from walt_price_bot.repository import ADMIN_TELEGRAM_ID, Repository


@pytest.mark.parametrize(
    ("text", "expected"),
    [("500", Decimal("500")), ("1 500,50 ₽", Decimal("1500.50"))],
)
def test_parse_money_is_convenient(text: str, expected: Decimal) -> None:
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["", "0", "-1", "не число"])
def test_parse_money_rejects_invalid_values(text: str) -> None:
    with pytest.raises(ValueError):
        parse_money(text)


def test_summary_interval_combines_hours_and_minutes_without_words() -> None:
    assert summary_interval_minutes(0, 1) == 1
    assert summary_interval_minutes(2, 30) == 150
    assert summary_interval_minutes(24, 0) == 1440
    with pytest.raises(ValueError):
        summary_interval_minutes(24, 1)


@pytest.mark.asyncio
async def test_preferences_remember_assets_limits_intervals_and_toggles(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.replace_user_assets(ADMIN_TELEGRAM_ID, (BTC, ETH))
    await repository.update_preferences(
        ADMIN_TELEGRAM_ID,
        directions=(QuoteSide.BUY,),
        min_limit_from=Decimal("1000"),
        min_limit_to=None,
        desired_amount=Decimal("25000"),
        monitor_interval_seconds=60,
        summary_interval_minutes=1,
        summary_enabled=True,
        show_exchange_rate=False,
        notify_threshold=False,
        quiet_enabled=True,
        quiet_start="23:00",
        quiet_end="07:30",
    )

    preferences = await repository.get_preferences(ADMIN_TELEGRAM_ID)
    assert preferences.assets == (BTC, ETH)
    assert preferences.summary_interval_minutes == 1
    assert preferences.min_limit_to is None
    assert preferences.notifications.threshold is False
    assert preferences.quiet_hours.start == "23:00"
    rendered = format_preferences(preferences)
    assert "BTC, ETH" in rendered
    assert "1 000 ₽ — без ограничения" in rendered
    assert "каждую 1 мин." in rendered


@pytest.mark.asyncio
async def test_asset_toggle_is_one_click_and_cannot_remove_last(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.replace_user_assets(ADMIN_TELEGRAM_ID, (BTC, ETH))

    assert await toggle_asset(repository, ADMIN_TELEGRAM_ID, BTC) == (ETH,)
    with pytest.raises(ValueError, match="хотя бы одну"):
        await toggle_asset(repository, ADMIN_TELEGRAM_ID, ETH)


def test_typed_settings_callback_round_trip() -> None:
    packed = SettingsCallback(action=SettingsAction.LIMIT, value="2000").pack()
    unpacked = SettingsCallback.unpack(packed)
    assert unpacked.action is SettingsAction.LIMIT
    assert unpacked.value == "2000"


def test_limit_menu_uses_valid_callback_values() -> None:
    keyboard = limit_settings_keyboard()
    callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]

    assert "settings:limit:500-2000" in callbacks


def test_settings_do_not_duplicate_notifications_section() -> None:
    callbacks = {
        button.callback_data
        for row in settings_keyboard().inline_keyboard
        for button in row
    }

    assert "settings:notifications:" not in callbacks


@pytest.mark.asyncio
async def test_notification_categories_show_state_and_return_to_notification_hub(
    tmp_path: Path,
) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    preferences = await repository.get_preferences(ADMIN_TELEGRAM_ID)

    keyboard = notification_categories_keyboard(preferences)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert buttons[0].text.startswith("✅ ")
    assert buttons[-1].callback_data == "menu:notifications"
    assert all("Сводки" not in button.text for button in buttons)

    await repository.update_preferences(ADMIN_TELEGRAM_ID, notify_threshold=False)
    keyboard = notification_categories_keyboard(
        await repository.get_preferences(ADMIN_TELEGRAM_ID)
    )
    assert keyboard.inline_keyboard[0][0].text.startswith("☐ ")


@pytest.mark.asyncio
async def test_settings_home_button_restores_main_menu() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    await return_to_main_menu(callback, state)

    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "💱 Текущие цены"
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_quiet_hours_button_opens_presets() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await show_quiet_hours(callback)

    callback.message.edit_text.assert_awaited_once()
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].text for row in markup.inline_keyboard[:-1]] == [
        "Выключить",
        "22:00–08:00",
        "23:00–07:00",
        "⚙️ Свое время",
    ]
    callback.answer.assert_awaited_once()


def test_custom_quiet_hours_use_button_time_picker() -> None:
    hours = time_picker_keyboard(SettingsAction.QUIET_START_HOUR, range(24))
    minutes = time_picker_keyboard(
        SettingsAction.QUIET_START_MINUTE, (0, 15, 30, 45)
    )

    assert [button.text for button in hours.inline_keyboard[0]] == [
        "00",
        "01",
        "02",
        "03",
        "04",
        "05",
    ]
    assert [button.text for button in minutes.inline_keyboard[0]] == [
        "00",
        "15",
        "30",
        "45",
    ]
    assert hours.inline_keyboard[-1][0].callback_data == "settings:quiet:"


@pytest.mark.asyncio
async def test_payment_methods_can_be_selected_individually(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()

    assert await toggle_payment_method(repository, ADMIN_TELEGRAM_ID, "sbp") == ("sbp",)
    assert await toggle_payment_method(repository, ADMIN_TELEGRAM_ID, "sberbankru") == (
        "sbp",
        "sberbankru",
    )
    assert await toggle_payment_method(repository, ADMIN_TELEGRAM_ID, "sbp") == (
        "sberbankru",
    )
    with pytest.raises(ValueError, match="хотя бы один"):
        await toggle_payment_method(repository, ADMIN_TELEGRAM_ID, "sberbankru")

    assert await toggle_payment_method(repository, ADMIN_TELEGRAM_ID, "any") == ("any",)
    preferences = await repository.get_preferences(ADMIN_TELEGRAM_ID)
    assert preferences.buy_payments == preferences.sell_payments == ("any",)


@pytest.mark.asyncio
async def test_payment_keyboard_shows_selected_method(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "bot.db")
    await repository.initialize()
    await toggle_payment_method(repository, ADMIN_TELEGRAM_ID, "tinkoff")

    keyboard = payment_methods_keyboard(
        await repository.get_preferences(ADMIN_TELEGRAM_ID)
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    t_bank = next(button for button in buttons if "Т-Банк" in button.text)
    assert t_bank.text.startswith("✅ ")
