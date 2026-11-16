from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class JobAction(CallbackData, prefix="job"):
    action: str  # approve | edit | rewrite | skip
    job_id: int


def job_card_keyboard(job_id: int, url: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Yuborish", callback_data=JobAction(action="approve", job_id=job_id))
    kb.button(text="✏️ Tahrirlash", callback_data=JobAction(action="edit", job_id=job_id))
    kb.button(text="🔁 Qayta yoz", callback_data=JobAction(action="rewrite", job_id=job_id))
    kb.button(text="⏭ O'tkazib yuborish", callback_data=JobAction(action="skip", job_id=job_id))
    if url:
        kb.button(text="🔗 Ochish", url=url)
    kb.adjust(3, 2)
    return kb.as_markup()
