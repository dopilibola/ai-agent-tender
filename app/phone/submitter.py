"""Queue: approved proposals on phone-enabled platforms → the phone places the bid → Telegram report + screenshot.

One bid at a time (the phone has ~1 GB free RAM). A bid is attempted once; only "phone offline" is retried
(a few times, silently after the first warning). In live mode the owner's approval is consumed right before
the agent starts, so one ✅ can never place two bids.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.core.approvals import ApprovalRequired, consume_approval
from app.core.states import JobStatus
from app.core.status import transition
from app.db.models import ApprovalKind, Job, Proposal
from app.phone.agent import AgentResult, BidAgent, BidFields
from app.phone.device import Phone, PhoneUnavailable
from app.tenders.timeutil import now_utc

log = logging.getLogger(__name__)
S = JobStatus
RETRY = {"phone_offline"}
MAX_ATTEMPTS = 6
PLATFORM = {"freelancer_com": "Freelancer.com", "kwork": "Kwork", "fl_ru": "FL.ru", "truelancer": "Truelancer",
            "upwork": "Upwork"}

HEAD = {
    "submitted": "📤 <b>Telefon topshirdi</b>",
    "ready": "🧪 <b>Sinov (dry-run): forma to'ldirildi, yuborilmadi</b>",
    "unconfirmed": "❓ <b>Submit bosildi, lekin tasdiq ko'rinmadi</b>",
    "need_human": "✋ <b>Telefon to'xtadi — sizning yordamingiz kerak</b>",
    "fail": "⚠️ <b>Telefon topshira olmadi</b>",
    "phone_offline": "📵 <b>Telefon boshqaruviga ulanib bo'lmadi</b>",
    "no_approval": "⛔ <b>Tasdiq topilmadi — yuborilmadi</b>",
}


class PhoneSubmitter:
    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker, gemini, notifier, *,
                 phone: Phone | None = None, agent: BidAgent | None = None):
        self.cfg = settings.config.phone
        self.sessionmaker = sessionmaker
        self.notifier = notifier
        self.phone = phone or Phone(self.cfg.adb_path, self.cfg.cdp_port, self.cfg.mdns_cmd)
        self.agent = agent or BidAgent(gemini, max_steps=self.cfg.max_steps, step_delay_s=self.cfg.step_delay_s)
        self._lock = asyncio.Lock()

    async def run_queue(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            for _ in range(3):
                nxt = await self._next()
                if nxt is None:
                    return
                if (await self.submit(*nxt)).status == "phone_offline":
                    return  # try again next minute, not three times in a row

    async def _next(self) -> tuple[int, int] | None:
        async with self.sessionmaker() as s:
            jobs = (await s.scalars(select(Job).where(Job.status == S.APPROVED, Job.platform.in_(self.cfg.platforms))
                                    .order_by(Job.id))).all()
            for job in jobs:
                p = await s.scalar(select(Proposal).where(Proposal.job_id == job.id)
                                   .order_by(Proposal.version.desc()).limit(1))
                if p is None or p.submitted_at is not None:
                    continue
                ph = (p.submit_result or {}).get("phone")
                if ph is None or (ph.get("status") in RETRY and ph.get("attempts", 0) < MAX_ATTEMPTS):
                    return job.id, p.id
        return None

    async def _save(self, proposal_id: int, **phone: Any) -> dict[str, Any]:
        async with self.sessionmaker() as s:
            p = await s.get(Proposal, proposal_id)
            prev = (p.submit_result or {}).get("phone", {})
            cur = {**prev, **phone, "at": now_utc().isoformat()}
            p.submit_result = {**(p.submit_result or {}), "phone": cur}
            await s.commit()
            return prev

    async def submit(self, job_id: int, proposal_id: int) -> AgentResult:
        live = not self.cfg.dry_run
        async with self.sessionmaker() as s:
            job = await s.get(Job, job_id)
            p = await s.get(Proposal, proposal_id)
            bid = BidFields(proposal=p.text, price=f"{p.price:g}", currency=p.currency,
                            days=str(p.timeline_days or job.timeline_days or 7))
            url, title, platform = job.url, job.title, job.platform
        prev = await self._save(proposal_id, status="running", live=live)
        attempts = prev.get("attempts", 0) + 1

        try:
            await self.phone.ensure()
        except PhoneUnavailable as e:
            res = AgentResult("phone_offline", str(e))
            await self._save(proposal_id, status=res.status, reason=res.reason, attempts=attempts)
            if attempts == 1:
                await self._report(job_id, title, platform, url, res,
                                   extra="Sozlamalar → Dasturchi parametrlari → <b>Simsiz disk raskadrovka</b>ni "
                                         "yoqing — o'zi qayta urinadi.")
            return res

        if live:
            try:
                async with self.sessionmaker() as s:
                    await consume_approval(s, kind=ApprovalKind.SUBMIT_PROPOSAL, target_type="proposal",
                                           target_id=proposal_id)
                    await s.commit()
            except ApprovalRequired as e:
                res = AgentResult("no_approval", str(e))
                await self._save(proposal_id, status=res.status, reason=res.reason, attempts=attempts)
                await self._report(job_id, title, platform, url, res)
                return res

        try:
            async with self.phone.open(url) as tab:
                res = await self.agent.run(tab, bid, live=live)
        except PhoneUnavailable as e:
            res = AgentResult("fail", f"Telefon: {e}")
        except Exception as e:
            log.exception("phone.submit_crashed", extra={"job": job_id})
            res = AgentResult("fail", f"{type(e).__name__}: {str(e)[:150]}")

        await self._save(proposal_id, status=res.status, reason=res.reason, steps=res.steps[-15:], attempts=attempts,
                         final_url=res.url)
        if res.status == "submitted":
            async with self.sessionmaker() as s:
                job = await s.get(Job, job_id)
                p = await s.get(Proposal, proposal_id)
                p.submitted_at = now_utc()
                if job.status == S.APPROVED:
                    await transition(s, job, S.SUBMITTED, "📱 telefon orqali topshirildi")
                await s.commit()
        log.info("phone.submit_done", extra={"job": job_id, "status": res.status, "reason": res.reason})
        await self._report(job_id, title, platform, url, res)
        return res

    async def _report(self, job_id: int, title: str, platform: str, url: str, res: AgentResult,
                      extra: str = "") -> None:
        lines = [HEAD.get(res.status, res.status),
                 f"#{job_id} · {html.escape(PLATFORM.get(platform, platform))} · {html.escape(title[:90])}",
                 html.escape(res.reason[:300])]
        if res.status == "ready":
            lines.append("Haqiqiy yuborish uchun: config.yaml → phone.dry_run: false")
        if res.status in ("need_human", "fail", "unconfirmed", "no_approval"):
            lines.append(f"Saytda oching: {html.escape(url)}")
        if extra:
            lines.append(extra)
        text = "\n".join(lines)
        try:
            if res.screenshot:
                await self.notifier.send_photo(res.screenshot, caption=text)
            else:
                await self.notifier.send(text)
        except Exception:
            log.exception("phone.report_failed")
