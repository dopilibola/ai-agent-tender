"""Outgoing Telegram messages — always and only to the owner chat."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)
TG_LIMIT = 4096


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # pathological single line
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) > limit:
            parts.append(buf)
            buf = ""
        buf += line
    if buf:
        parts.append(buf)
    return parts


class Notifier:
    def __init__(self, bot: Bot, owner_chat_id: int) -> None:
        self.bot = bot
        self.chat_id = owner_chat_id

    async def send(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *,
        disable_preview: bool = True,
    ) -> Message:
        chunks = split_text(text)
        msg: Message | None = None
        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == len(chunks) - 1 else None
            for _ in range(3):
                try:
                    msg = await self.bot.send_message(
                        self.chat_id,
                        chunk,
                        reply_markup=markup,
                        disable_web_page_preview=disable_preview,
                    )
                    break
                except TelegramRetryAfter as e:
                    log.warning("telegram.flood_wait", extra={"retry_after": e.retry_after})
                    await asyncio.sleep(e.retry_after + 1)
            else:
                raise RuntimeError("Telegram flood-wait: message not sent after 3 attempts")
        assert msg is not None
        return msg

    async def send_photo(self, photo: bytes, caption: str = "") -> None:
        from aiogram.types import BufferedInputFile

        await self.bot.send_photo(self.chat_id, BufferedInputFile(photo, "screen.png"), caption=caption[:1024])

    async def send_text(self, text: str) -> None:
        """Signature-compatible with app.core.status.SendFn."""
        await self.send(text)
