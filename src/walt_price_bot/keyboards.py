from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from .models import UserRole

PRICES_BUTTON = "💱 Текущие цены"
NOTIFICATIONS_BUTTON = "🔔 Уведомления"
SETTINGS_BUTTON = "⚙️ Настройки"
ADMIN_BUTTON = "👥 Пользователи"


def main_navigation_keyboard(role: UserRole = UserRole.USER) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=PRICES_BUTTON)],
        [
            KeyboardButton(text=NOTIFICATIONS_BUTTON),
            KeyboardButton(text=SETTINGS_BUTTON),
        ],
    ]
    if role is UserRole.ADMIN:
        rows.append([KeyboardButton(text=ADMIN_BUTTON)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел",
    )


def phone_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Подтвердите номер телефона",
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
