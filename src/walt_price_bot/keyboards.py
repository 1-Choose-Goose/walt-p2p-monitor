from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from .models import UserRole

PRICES_BUTTON = "💱 Текущие цены"
NOTIFICATIONS_BUTTON = "🔔 Уведомления"
SETTINGS_BUTTON = "⚙️ Настройки"
ADMIN_BUTTON = "👥 Пользователи"
MAIN_MENU_BUTTON = "🏠 Главное меню"


def main_navigation_keyboard(role: UserRole = UserRole.USER) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MAIN_MENU_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Нажмите, чтобы открыть меню",
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
