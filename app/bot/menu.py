"""Two always-visible buttons that split the bot into its two worlds."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

FREELANCE_BTN = "💼 Frilans"
TENDERS_BTN = "🏛 Tenderlar"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=FREELANCE_BTN), KeyboardButton(text=TENDERS_BTN)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Bo'limni tanlang…",
    )
