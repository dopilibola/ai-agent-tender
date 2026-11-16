"""Apply job status transitions and deliver their Telegram notifications.

`transition()` writes a StatusEvent inside the caller's transaction. Notifications are sent
afterwards by `flush_notifications()`, so a Telegram outage never loses an event: unsent
events are retried on the next run.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.states import STATUS_LABEL_UZ, JobStatus, assert_transition
from app.db.models import Job, StatusEvent

log = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]


async def transition(
    session: AsyncSession, job: Job, to: JobStatus, note: str = ""
) -> StatusEvent:
    assert_transition(job.status, to)
    event = StatusEvent(job_id=job.id, from_status=job.status, to_status=to, note=note)
    job.status = to
    session.add(event)
    await session.flush()
    log.info(
        "job.transition",
        extra={"job_id": job.id, "from": event.from_status, "to": to, "note": note},
    )
    return event


# Statuses not worth a Telegram ping on their own: automatic triage (the job card covers the
# interesting ones) and my own button clicks (already answered in the chat).
QUIET = {
    JobStatus.SCORED, JobStatus.SKIPPED, JobStatus.DRAFTED, JobStatus.AWAITING_APPROVAL,
    JobStatus.APPROVED, JobStatus.EXPIRED,
}


def format_event(event: StatusEvent) -> str:
    job = event.job
    label = STATUS_LABEL_UZ[JobStatus(event.to_status)]
    text = f"{label}\n<b>{html.escape(job.title)}</b> · {html.escape(job.platform)} · #{job.id}"
    if event.note:
        text += f"\n{html.escape(event.note)}"
    return text


async def flush_notifications(sessionmaker: async_sessionmaker, send: SendFn) -> int:
    sent = 0
    async with sessionmaker() as session:
        events = (
            await session.scalars(
                select(StatusEvent)
                .where(StatusEvent.notified.is_(False))
                .options(selectinload(StatusEvent.job))
                .order_by(StatusEvent.id)
                .limit(50)
            )
        ).all()
        for event in events:
            if event.to_status not in QUIET:
                try:
                    await send(format_event(event))
                except Exception:
                    log.exception("notify.failed", extra={"event_id": event.id})
                    break  # keep order; retry later
                sent += 1
            event.notified = True
            await session.commit()
    return sent
