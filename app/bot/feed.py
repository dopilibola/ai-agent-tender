"""Mirror of outgoing Telegram notifications into the DB, so the website shows exactly the same feed.

`FeedSender` wraps the Notifier with a fixed `kind` (tender / freelance / email / status). A message is stored
only after Telegram accepted it, so the site never shows something Telegram did not get.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import SentMessage

log = logging.getLogger(__name__)

KINDS = ("tender", "freelance", "email", "status")


def buttons_of(markup: InlineKeyboardMarkup | None) -> list[list[dict[str, Any]]]:
    if markup is None:
        return []
    rows = []
    for row in markup.inline_keyboard:
        rows.append([{k: v for k, v in {"text": b.text, "url": b.url, "cb": b.callback_data}.items() if v}
                     for b in row])
    return rows


async def record(sessionmaker: async_sessionmaker, kind: str, text: str, markup: InlineKeyboardMarkup | None = None,
                 *, at: datetime | None = None) -> None:
    try:
        async with sessionmaker() as s:
            row = SentMessage(kind=kind, text=text, buttons=buttons_of(markup))
            if at is not None:
                row.created_at = at
            s.add(row)
            await s.commit()
    except Exception:
        log.exception("feed.record_failed", extra={"kind": kind})


class FeedSender:
    """Same interface as Notifier (send / send_text), plus a copy in `sent_messages`."""

    def __init__(self, notifier, sessionmaker: async_sessionmaker, kind: str):
        self.notifier, self.sessionmaker, self.kind = notifier, sessionmaker, kind

    async def send(self, text: str, reply_markup: InlineKeyboardMarkup | None = None, **kw):
        msg = await self.notifier.send(text, reply_markup, **kw)
        await record(self.sessionmaker, self.kind, text, reply_markup)
        return msg

    async def send_text(self, text: str) -> None:
        await self.send(text)


async def backfill(sessionmaker: async_sessionmaker, limit_usd: float = 0.0) -> int:
    """First start with the feed: rebuild tender cards and pending job cards that Telegram already got."""
    from app.bot.cards import format_job_card
    from app.bot.keyboards import job_card_keyboard
    from app.core.states import JobStatus
    from app.db.models import Job, Lot, Proposal
    from app.tenders import cards

    async with sessionmaker() as s:
        if await s.scalar(select(func.count()).select_from(SentMessage)):
            return 0
        lots = (await s.scalars(select(Lot).where(Lot.notified_at.is_not(None)).order_by(Lot.notified_at))).all()
        jobs = (await s.scalars(select(Job).where(Job.status == JobStatus.AWAITING_APPROVAL))).all()
        rows = [SentMessage(kind="tender", text=cards.lot_card(lot, limit_usd=limit_usd),
                            buttons=buttons_of(cards.lot_keyboard(lot)), created_at=lot.notified_at) for lot in lots]
        for job in jobs:
            proposal = await s.scalar(select(Proposal).where(Proposal.job_id == job.id)
                                      .order_by(Proposal.version.desc()))
            rows.append(SentMessage(kind="freelance", text=format_job_card(job, proposal),
                                    buttons=buttons_of(job_card_keyboard(job.id, job.url)), created_at=job.updated_at))
        s.add_all(rows)
        await s.commit()
    log.info("feed.backfilled", extra={"messages": len(rows)})
    return len(rows)
