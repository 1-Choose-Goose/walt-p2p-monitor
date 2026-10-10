from decimal import Decimal

import pytest

from walt_price_bot.models import Asset, MarketSnapshot, P2PAd, QuoteSide
from walt_price_bot.ui import (
    asset_keyboard,
    format_market_summary,
    main_menu_keyboard,
    parse_daily_time,
    parse_positive_decimal,
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
    )

    text = format_market_summary([snapshot])

    assert "USDT / RUB" in text
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

    assert text.count("Подходящих онлайн-объявлений сейчас нет.") == 3
    assert "BTC / RUB" in text


def test_main_menu_has_clear_russian_visual_actions() -> None:
    keyboard = main_menu_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [button.callback_data for button in buttons] == [
        "menu:price",
        "menu:alert",
        "menu:change",
        "menu:schedule",
        "menu:alerts",
        "menu:delete",
        "menu:pause",
        "menu:resume",
        "menu:help",
    ]
    assert all(any("А" <= char <= "я" for char in button.text) for button in buttons)


def test_asset_keyboard_includes_bitcoin_in_russian() -> None:
    buttons = [button for row in asset_keyboard("alert").inline_keyboard for button in row]

    assert [(button.text, button.callback_data) for button in buttons] == [
        ("USDT", "alert:asset:USDT"),
        ("TON", "alert:asset:TON"),
        ("Биткоин", "alert:asset:BTC"),
    ]


@pytest.mark.parametrize("value", ["", "0", "-1", "nan", "1e20", "abc"])
def test_positive_decimal_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_positive_decimal(value, maximum=Decimal("1000000000"))


def test_positive_decimal_accepts_russian_comma() -> None:
    assert parse_positive_decimal(" 90,25 ") == Decimal("90.25")


@pytest.mark.parametrize("value", ["9:30", "24:00", "12:99", "abc"])
def test_daily_time_requires_hh_mm(value: str) -> None:
    with pytest.raises(ValueError):
        parse_daily_time(value)


def test_daily_time_accepts_valid_clock() -> None:
    assert parse_daily_time("09:30") == "09:30"
