from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from walt_price_bot.models import (
    Asset,
    MarketSnapshot,
    OpportunityHistoryEntry,
    OpportunityMode,
    OpportunitySettings,
    P2PAd,
    QuoteSide,
)
from walt_price_bot.ui import (
    asset_keyboard,
    delete_keyboard,
    direction_keyboard,
    format_market_summary,
    format_opportunity_settings,
    main_menu_keyboard,
    notifications_menu_keyboard,
    opportunity_history_detail_keyboard,
    opportunity_history_keyboard,
    opportunity_mode_keyboard,
    opportunity_premium_keyboard,
    opportunity_threshold_keyboard,
    parse_daily_time,
    parse_positive_decimal,
    schedule_keyboard,
    side_keyboard,
)


def quote(identifier: str, side: QuoteSide, price: str) -> P2PAd:
    return P2PAd(
        id=identifier,
        nickname="Trader",
        asset=Asset.USDT,
        side=side,
        price=Decimal(price),
        min_amount=Decimal("1000"),
        max_amount=Decimal("50000"),
        payments=("sberbank", "tinkoff"),
        rating=Decimal("0.9875"),
        is_online=True,
        number="A1B2C3",
        user_id="423113846849714",
    )


def test_market_summary_shows_ruble_buy_sell_and_offer_details() -> None:
    snapshot = MarketSnapshot(
        asset=Asset.USDT,
        buy=quote("11668659858949", QuoteSide.SELL, "89.50"),
        sell=quote("buy-ad", QuoteSide.BUY, "90.10"),
        exchange_price=Decimal("85.05"),
    )

    text = format_market_summary([snapshot])

    assert "USDT / RUB" in text
    assert "Биржевой курс (Coinbase): 85,05 ₽" in text
    assert "Купить: 89,50 ₽" in text
    assert "Продать: 90,10 ₽" in text
    assert "1 000–50 000 ₽" in text
    assert "Сбербанк, Т-Банк" in text
    assert "Trader" in text
    assert (
        'href="https://telegram.me/walt/start?startapp='
        'v2-b3BlcmF0aW9uPW9mZmVyaWRfMTE2Njg2NTk4NTg5NDlfNDIzMTEzODQ2ODQ5NzE0"' in text
    )
    assert "Открыть объявление" in text
    assert "Объявление №A1B2C3" in text


def test_market_summary_escapes_untrusted_walt_text() -> None:
    unsafe = quote("sell-ad", QuoteSide.SELL, "89.50")
    unsafe = P2PAd(
        id=unsafe.id,
        user_id=unsafe.user_id,
        nickname="<b>fake</b>",
        asset=unsafe.asset,
        side=unsafe.side,
        price=unsafe.price,
        min_amount=unsafe.min_amount,
        max_amount=unsafe.max_amount,
        payments=("bank<script>",),
        rating=unsafe.rating,
        is_online=True,
    )
    snapshot = MarketSnapshot(asset=Asset.USDT, buy=unsafe, sell=unsafe)

    text = format_market_summary([snapshot])

    assert "&lt;b&gt;fake&lt;/b&gt;" in text
    assert "bank&lt;script&gt;" in text
    assert "<b>fake</b>" not in text


def test_market_summary_explains_when_filtered_offer_is_unavailable() -> None:
    text = format_market_summary([])

    assert text.count("Подходящих онлайн-объявлений сейчас нет.") == 5
    assert "GRAM / RUB" in text
    assert "BTC / RUB" in text


def test_market_summary_shows_exchange_rate_without_matching_p2p_offer() -> None:
    snapshot = MarketSnapshot(
        asset=Asset.USDT,
        buy=None,
        sell=None,
        exchange_price=Decimal("85.05"),
    )

    text = format_market_summary([snapshot])

    assert "Биржевой курс (Coinbase): 85,05 ₽" in text
    assert "Купить: подходящих P2P-объявлений нет" in text
    assert "Продать: подходящих P2P-объявлений нет" in text


def test_market_summary_hides_exchange_rate_when_user_disabled_it() -> None:
    snapshot = MarketSnapshot(
        asset=Asset.USDT,
        buy=None,
        sell=None,
        exchange_price=Decimal("85.10"),
    )

    text = format_market_summary([snapshot], show_exchange_rate=False)

    assert "Coinbase" not in text
    assert "85,10" not in text


def test_market_summary_hides_unselected_sell_direction() -> None:
    snapshot = MarketSnapshot(
        asset=Asset.USDT,
        buy=None,
        sell=None,
        exchange_price=Decimal("85.05"),
    )

    text = format_market_summary([snapshot], directions=(QuoteSide.BUY,))

    assert "Купить:" in text
    assert "Продать:" not in text


def test_main_menu_has_clear_russian_visual_actions() -> None:
    keyboard = main_menu_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [button.callback_data for button in buttons] == [
        "menu:price",
        "menu:notifications",
        "settings:open:",
    ]
    assert buttons[0].text == "💱 Текущие цены"
    assert all(any("А" <= char <= "я" for char in button.text) for button in buttons)


def test_notification_actions_are_grouped_and_markup_is_user_selectable() -> None:
    buttons = [
        button
        for row in notifications_menu_keyboard(paused=False).inline_keyboard
        for button in row
    ]
    callbacks = {button.callback_data for button in buttons}
    assert {
        "menu:opportunity",
        "menu:opportunity-history",
        "menu:alert",
        "menu:change",
        "settings:summary:",
    } <= callbacks
    assert "settings:notifications:" in callbacks
    assert "menu:pause" in callbacks
    threshold_texts = [
        button.text
        for row in opportunity_threshold_keyboard().inline_keyboard
        for button in row
    ]
    assert "Своя наценка" in threshold_texts


def test_opportunity_history_buttons_open_entry_and_return_to_notifications() -> None:
    created_at = datetime(2026, 10, 11, 3, 31, tzinfo=UTC)
    entry = OpportunityHistoryEntry(
        id=17,
        asset=Asset.USDT,
        quote_side=QuoteSide.BUY,
        ad_id="offer-a",
        message_text="Выгодный лот USDT",
        notified_at=created_at,
        expires_at=created_at + timedelta(minutes=10),
    )

    history_buttons = [
        button
        for row in opportunity_history_keyboard([entry]).inline_keyboard
        for button in row
    ]
    detail_buttons = [
        button
        for row in opportunity_history_detail_keyboard().inline_keyboard
        for button in row
    ]

    assert history_buttons[0].callback_data == "opportunity-history:open:17:0"
    assert "Покупка USDT" in history_buttons[0].text
    assert history_buttons[-1].callback_data == "menu:notifications"
    assert detail_buttons[0].callback_data == "menu:opportunity-history:0"


def test_asset_keyboard_uses_current_walt_p2p_tickers() -> None:
    buttons = [button for row in asset_keyboard("alert").inline_keyboard for button in row]

    assert [(button.text, button.callback_data) for button in buttons[:-1]] == [
        ("GRAM", "alert:asset:GRAM"),
        ("USDT", "alert:asset:USDT"),
        ("BTC", "alert:asset:BTC"),
        ("NOT", "alert:asset:NOT"),
        ("ETH", "alert:asset:ETH"),
    ]


def test_flow_keyboards_have_back_to_main_menu_button() -> None:
    keyboards = [
        asset_keyboard("alert"),
        side_keyboard("alert"),
        direction_keyboard(),
        schedule_keyboard(),
        delete_keyboard([], []),
        opportunity_mode_keyboard(),
        opportunity_premium_keyboard(),
        opportunity_threshold_keyboard(),
    ]

    for keyboard in keyboards:
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        assert any(
            button.text == "⬅️ Назад в меню" and button.callback_data == "menu:home"
            for button in buttons
        )


def test_opportunity_controls_are_fully_russian_and_show_current_status() -> None:
    settings = OpportunitySettings(
        enabled=True,
        mode=OpportunityMode.BOTH,
        min_spread_percent=Decimal("2"),
    )

    text = format_opportunity_settings(settings)
    mode_buttons = [
        button for row in opportunity_mode_keyboard(settings).inline_keyboard for button in row
    ]
    threshold_buttons = [
        button for row in opportunity_threshold_keyboard().inline_keyboard for button in row
    ]

    assert "включены" in text
    assert "покупка и продажа" in text
    assert "2%" in text
    assert {button.callback_data for button in mode_buttons} >= {
        "opportunity:mode:BUY",
        "opportunity:mode:SELL",
        "opportunity:mode:BOTH",
        "opportunity:threshold-menu",
        "opportunity:premium-menu",
        "opportunity:disable",
    }
    assert any("Минимум для продажи: 2%" in button.text for button in mode_buttons)
    assert any("Переплата при покупке: до 2%" in button.text for button in mode_buttons)
    assert any("рекомендуется" in button.text for button in threshold_buttons)


@pytest.mark.parametrize("value", ["", "0", "-1", "nan", "1e20", "abc"])
def test_positive_decimal_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_positive_decimal(value, maximum=Decimal("1000000000"))


def test_positive_decimal_accepts_russian_comma() -> None:
    assert parse_positive_decimal(" 90,25 ") == Decimal("90.25")


def test_positive_decimal_honors_lower_bound() -> None:
    with pytest.raises(ValueError):
        parse_positive_decimal("0,09", minimum=Decimal("0.1"))
    assert parse_positive_decimal("0,1", minimum=Decimal("0.1")) == Decimal("0.1")


@pytest.mark.parametrize("value", ["9:30", "24:00", "12:99", "abc"])
def test_daily_time_requires_hh_mm(value: str) -> None:
    with pytest.raises(ValueError):
        parse_daily_time(value)


def test_daily_time_accepts_valid_clock() -> None:
    assert parse_daily_time("09:30") == "09:30"
