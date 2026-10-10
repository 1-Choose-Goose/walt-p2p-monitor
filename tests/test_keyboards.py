from aiogram.types import ReplyKeyboardMarkup

from walt_price_bot.keyboards import (
    ADMIN_BUTTON,
    NOTIFICATIONS_BUTTON,
    PRICES_BUTTON,
    SETTINGS_BUTTON,
    main_navigation_keyboard,
)
from walt_price_bot.models import UserRole


def test_main_navigation_keyboard_is_persistent_and_grouped() -> None:
    keyboard = main_navigation_keyboard(UserRole.USER)

    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.is_persistent is True
    assert keyboard.resize_keyboard is True
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        [PRICES_BUTTON],
        [NOTIFICATIONS_BUTTON, SETTINGS_BUTTON],
    ]


def test_admin_navigation_keyboard_adds_user_management() -> None:
    keyboard = main_navigation_keyboard(UserRole.ADMIN)

    assert [button.text for button in keyboard.keyboard[-1]] == [ADMIN_BUTTON]
