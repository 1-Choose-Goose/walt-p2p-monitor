from aiogram.types import ReplyKeyboardMarkup

from walt_price_bot.keyboards import (
    MAIN_MENU_BUTTON,
    main_navigation_keyboard,
)
from walt_price_bot.models import UserRole


def test_main_navigation_keyboard_is_one_persistent_restore_button() -> None:
    keyboard = main_navigation_keyboard(UserRole.USER)

    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.is_persistent is True
    assert keyboard.resize_keyboard is True
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        [MAIN_MENU_BUTTON]
    ]


def test_admin_uses_the_same_compact_restore_keyboard() -> None:
    keyboard = main_navigation_keyboard(UserRole.ADMIN)

    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        [MAIN_MENU_BUTTON]
    ]
