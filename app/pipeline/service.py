"""💼 Freelance pipeline: scout → hard filters → Gemini score → Gemini proposal → Telegram card.

Nothing is sent to a client from here. "✅ Yuborish" records my approval; while a connector cannot
submit (no API token / dry-run) the approved text is handed back to me to paste on the site.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.cards import format_job_card
from app.bot.keyboards import job_card_keyboard
from app.config import Settings
from app.connectors.base import Connector, JobListing
from app.core.approvals import decide, request_approval
from app.core.states import JobStatus
from app.core.status import transition
from app.db.models import KV, Approval, ApprovalKind, ApprovalStatus, Job, LLMCall, Proposal
from app.llm.gemini import GeminiClient
from app.pipeline import ai
from app.pipeline.filters import hard_filter
from app.tenders.timeutil import TASHKENT, as_utc, fmt_local, now_utc

log = logging.getLogger(__name__)
S = JobStatus
LLM_PURPOSES = ("job_scoring", "proposal", "email_extract")


class Sender(Protocol):
    async def send(self, text: str, reply_markup=None, **kw) -> Any: ...


@dataclass
class CycleStats:
    fetched: int = 0
    new: int = 0
    filtered: int = 0
    scored: int = 0
    skipped_by_ai: int = 0
    drafted: int = 0


class FreelanceService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker,
        connectors: dict[str, Connector],
        gemini: GeminiClient,
        sender: Sender,
        *,
        fx=None,  # app.tenders.fx.UsdRate — converts RUB/INR/... budgets to USD for the filters
    ) -> None:
        self.fx = fx
        self.settings = settings
        self.cfg = settings.config
        self.profile = settings.profile
        self.sessionmaker = sessionmaker
        self.connectors = connectors
        self.gemini = gemini
        self.sender = sender
        self._lock = asyncio.Lock()
        self._last_fetch: dict[str, datetime] = {}
        self.next_run_at: Callable[[], datetime | None] = lambda: None
        self.score: Callable[..., Awaitable[ai.JobScore]] = ai.score_job
        self.write: Callable[..., Awaitable[tuple[ai.ProposalDraft, str]]] = ai.write_proposal

    @property
    def running(self) -> bool:
        return self._lock.locked()

    # ── Limits ──────────────────────────────────────────────────────────────
    @staticmethod
    def _local_midnight_utc(now: datetime) -> datetime:
        return datetime.combine(now.astimezone(TASHKENT).date(), time(0), TASHKENT).astimezone(now.tzinfo)

    async def llm_calls_today(self, s: AsyncSession, now: datetime) -> int:
        return await s.scalar(
            select(func.count()).select_from(LLMCall).where(
                LLMCall.purpose.in_(LLM_PURPOSES), LLMCall.created_at >= self._local_midnight_utc(now)
            )
        ) or 0

    async def drafts_today(self, s: AsyncSession, now: datetime) -> int:
        return await s.scalar(
            select(func.count(func.distinct(Proposal.job_id))).where(
                Proposal.created_at >= self._local_midnight_utc(now), Proposal.version == 1
            )
        ) or 0

    # ── Cycle ───────────────────────────────────────────────────────────────
    def interval_min(self, name: str) -> int:
        if name == "email":
            return self.cfg.email_watch.poll_interval_min
        pcfg = self.cfg.platforms.get(name)
        return pcfg.poll_interval_min if pcfg else 10

    async def run_cycle(self, now: datetime | None = None, *, force: bool = False) -> CycleStats:
        async with self._lock:
            now = now or now_utc()
            stats = CycleStats()
            for name, conn in self.connectors.items():
                last = self._last_fetch.get(name)
                if not force and last and now - last < timedelta(minutes=self.interval_min(name), seconds=-30):
                    continue  # each platform keeps its own pace (e.g. Truelancer hourly)
                self._last_fetch[name] = now
                try:
                    listings = await conn.fetch_jobs()
                except Exception as e:
                    log.exception("freelance.fetch_failed", extra={"platform": name})
                    await self._health(name, conn.title, error=e)
                    continue
                await self._health(name, conn.title, count=len(listings))
                stats.fetched += len(listings)
                await self._store_new(name, listings, now, stats)
            await self._process_pending(now, stats)
            log.info("freelance.cycle", extra=stats.__dict__)
            return stats

    async def _store_new(self, platform: str, listings: list[JobListing], now: datetime, stats: CycleStats) -> None:
        # One connector may yield several platforms (Gmail → upwork / fiverr): dedupe per item.platform.
        async with self.sessionmaker() as s:
            platforms = {item.platform for item in listings} | {platform}
            known = {
                (p, e) for p, e in (await s.execute(
                    select(Job.platform, Job.external_id).where(Job.platform.in_(platforms))
                )).all()
            }
            for item in listings:
                key = (item.platform, item.external_id)
                if key in known:
                    continue
                known.add(key)
                pcfg = self.cfg.platforms.get(item.platform)
                if item.usd_rate is None and self.fx is not None:
                    try:
                        item.usd_rate = await self.fx.usd_per_unit(item.currency)
                    except Exception:
                        log.warning("freelance.fx_failed", extra={"currency": item.currency})
                job = Job(
                    platform=item.platform, external_id=item.external_id, url=item.url, title=item.title[:500],
                    description=item.description, language=item.language, budget_min=item.budget_min,
                    budget_max=item.budget_max, currency=item.currency, budget_usd=item.budget_usd,
                    budget_type=item.budget_type, proposals_count=item.proposals_count,
                    posted_at=item.posted_at, skills=item.skills, client_country=item.client_country,
                    client_rating=item.client_rating, client_total_spent_usd=item.client_total_spent_usd,
                    client_payment_verified=item.client_payment_verified, status=S.FOUND,
                    raw={**item.raw, "usd_rate": item.usd_rate},
                )
                s.add(job)
                await s.flush()
                stats.new += 1
                reason = hard_filter(item, self.cfg, self.profile, pcfg, now)
                if reason:
                    await transition(s, job, S.SKIPPED, f"filtr: {reason}")
                    stats.filtered += 1
            await s.commit()

    async def _process_pending(self, now: datetime, stats: CycleStats) -> None:
        """Score FOUND jobs and draft proposals for good SCORED ones, within the daily limits."""
        max_age = now - timedelta(hours=self.cfg.filters.max_job_age_hours)
        async with self.sessionmaker() as s:
            found = (await s.scalars(
                select(Job.id).where(Job.status == S.FOUND).order_by(Job.posted_at.desc())
                .limit(self.cfg.max_scores_per_cycle)
            )).all()
        for job_id in found:
            async with self.sessionmaker() as s:
                if await self.llm_calls_today(s, now) >= self.cfg.max_llm_calls_per_day:
                    log.warning("freelance.llm_daily_cap")
                    break
                job = await s.get(Job, job_id)
                if job.posted_at and as_utc(job.posted_at) < max_age:
                    await transition(s, job, S.EXPIRED, "baholanmasdan eskirdi")
                    await s.commit()
                    continue
                try:
                    score = await self.score(self.gemini, job, self.profile, self.profile.min_rate_usd_per_hour)
                except Exception as e:
                    log.warning("freelance.score_failed", extra={"job": job.id, "error": str(e)[:200]})
                    continue  # stays FOUND → retried next cycle
                job.fit, job.win_probability = score.fit, score.win_probability
                job.effort_hours, job.risk_flags = score.effort_hours, score.risk_flags
                job.summary_uz, job.recommended = score.summary_uz, score.recommended
                await transition(s, job, S.SCORED, f"fit {score.fit}, yutish {score.win_probability}%")
                stats.scored += 1
                bad = set(score.risk_flags) & ai.AUTO_SKIP_FLAGS
                if bad or not score.recommended or score.fit < self.cfg.min_fit_score:
                    why = (f"xavf: {', '.join(sorted(bad))}" if bad else
                           "AI tavsiya qilmadi" if not score.recommended else f"fit {score.fit} < {self.cfg.min_fit_score}")
                    await transition(s, job, S.SKIPPED, why)
                    stats.skipped_by_ai += 1
                await s.commit()

        async with self.sessionmaker() as s:
            to_draft = (await s.scalars(
                select(Job.id).where(Job.status == S.SCORED, Job.recommended.is_(True))
                .order_by(Job.fit.desc())
            )).all()
        for job_id in to_draft:
            async with self.sessionmaker() as s:
                if await self.drafts_today(s, now) >= self.cfg.max_proposals_per_day:
                    break
                if await self.llm_calls_today(s, now) >= self.cfg.max_llm_calls_per_day:
                    break
                job = await s.get(Job, job_id)
                if job.posted_at and as_utc(job.posted_at) < max_age:
                    await transition(s, job, S.EXPIRED, "taklif yozilmasdan eskirdi")
                    await s.commit()
                    continue
                try:
                    draft, version = await self.write(self.gemini, job, self.profile)
                except Exception as e:
                    log.warning("freelance.write_failed", extra={"job": job.id, "error": str(e)[:200]})
                    continue
                proposal = await self._save_proposal(s, job, draft, version)
                await transition(s, job, S.DRAFTED)
                await transition(s, job, S.AWAITING_APPROVAL)
                await s.commit()
                await self._send_card(job, proposal)
                stats.drafted += 1

    async def _save_proposal(self, s: AsyncSession, job: Job, draft: ai.ProposalDraft, prompt_version: str) -> Proposal:
        last = await s.scalar(select(func.max(Proposal.version)).where(Proposal.job_id == job.id)) or 0
        proposal = Proposal(
            job_id=job.id, version=last + 1, text=draft.text.strip(), price=draft.price, currency=job.currency or "USD",
            deposit=None, timeline_days=draft.timeline_days,
            milestones=[m.model_dump() for m in draft.milestones], prompt_version=prompt_version,
        )
        s.add(proposal)
        await s.flush()
        # A new version supersedes any pending approval of an older one.
        await s.execute(
            update(Approval)
            .where(Approval.job_id == job.id, Approval.status == ApprovalStatus.PENDING)
            .values(status=ApprovalStatus.REJECTED, decided_at=now_utc())
        )
        await request_approval(
            s, kind=ApprovalKind.SUBMIT_PROPOSAL, target_type="proposal", target_id=proposal.id,
            job_id=job.id, payload={"version": proposal.version},
        )
        job.quote, job.timeline_days, job.deposit = proposal.price, proposal.timeline_days, proposal.deposit
        return proposal

    async def _send_card(self, job: Job, proposal: Proposal | None) -> None:
        try:
            await self.sender.send(format_job_card(job, proposal), reply_markup=job_card_keyboard(job.id, job.url))
        except Exception:
            log.exception("freelance.card_failed", extra={"job": job.id})

    # ── Owner actions (from Telegram buttons) ──────────────────────────────
    async def latest(self, s: AsyncSession, job_id: int) -> tuple[Job | None, Proposal | None]:
        job = await s.get(Job, job_id)
        if job is None:
            return None, None
        proposal = await s.scalar(
            select(Proposal).where(Proposal.job_id == job_id).order_by(Proposal.version.desc())
        )
        return job, proposal

    async def approve(self, job_id: int, user_id: int) -> str:
        async with self.sessionmaker() as s:
            job, proposal = await self.latest(s, job_id)
            if job is None or proposal is None:
                return "Ish topilmadi."
            if job.status != S.AWAITING_APPROVAL:
                return f"Bu ish holati: {job.status} — tasdiqlab bo'lmaydi."
            approval = await s.scalar(select(Approval).where(
                Approval.target_type == "proposal", Approval.target_id == proposal.id,
                Approval.status == ApprovalStatus.PENDING))
            if approval is None:
                return "Tasdiq yozuvi topilmadi (eski karta?)."
            await decide(s, approval.id, approved=True, user_id=user_id, owner_id=self.settings.owner_chat_id)
            await transition(s, job, S.APPROVED, f"taklif v{proposal.version} tasdiqlandi")
            await s.commit()
            conn = self.connectors.get(job.platform)
            pcfg = self.cfg.platforms.get(job.platform)
        if conn is None or not conn.can_submit or (pcfg and pcfg.dry_run):
            return "manual"
        return "queued"  # submitter (next step) will pick it up

    async def skip(self, job_id: int, user_id: int) -> str:
        async with self.sessionmaker() as s:
            job = await s.get(Job, job_id)
            if job is None:
                return "Ish topilmadi."
            pending = (await s.scalars(select(Approval).where(
                Approval.job_id == job_id, Approval.status == ApprovalStatus.PENDING))).all()
            for a in pending:
                await decide(s, a.id, approved=False, user_id=user_id, owner_id=self.settings.owner_chat_id)
            if job.status in (S.AWAITING_APPROVAL, S.SCORED, S.DRAFTED, S.FOUND):
                await transition(s, job, S.SKIPPED, "o'zim o'tkazib yubordim")
            await s.commit()
        return "ok"

    async def replace_text(self, job_id: int, text: str) -> tuple[Job | None, Proposal | None]:
        async with self.sessionmaker() as s:
            job, current = await self.latest(s, job_id)
            if job is None or current is None or job.status != S.AWAITING_APPROVAL:
                return None, None
            draft = ai.ProposalDraft(text=text, price=current.price, timeline_days=current.timeline_days or 1,
                                     milestones=[ai.Milestone(**m) for m in current.milestones or []])
            proposal = await self._save_proposal(s, job, draft, "manual")
            await transition(s, job, S.DRAFTED, "matnni o'zim tahrirladim")
            await transition(s, job, S.AWAITING_APPROVAL)
            await s.commit()
            return job, proposal

    async def rewrite(self, job_id: int, instruction: str) -> tuple[Job | None, Proposal | None]:
        async with self.sessionmaker() as s:
            job, current = await self.latest(s, job_id)
            if job is None or current is None or job.status != S.AWAITING_APPROVAL:
                return None, None
            draft, version = await self.write(self.gemini, job, self.profile, current=current, instruction=instruction)
            proposal = await self._save_proposal(s, job, draft, version)
            await transition(s, job, S.DRAFTED, f"qayta yozildi: {instruction[:80]}")
            await transition(s, job, S.AWAITING_APPROVAL)
            await s.commit()
            return job, proposal

    async def card(self, job_id: int) -> tuple[Job | None, str]:
        async with self.sessionmaker() as s:
            job, proposal = await self.latest(s, job_id)
            if job is None:
                return None, "Bunday ish yo'q. /jobs"
            return job, format_job_card(job, proposal)

    # ── Lists / status / summary ───────────────────────────────────────────
    async def recent(self, limit: int = 10, statuses: tuple[JobStatus, ...] | None = None) -> list[Job]:
        async with self.sessionmaker() as s:
            q = select(Job).order_by(Job.id.desc()).limit(limit)
            if statuses:
                q = q.where(Job.status.in_(statuses))
            else:
                q = q.where(Job.status.not_in((S.SKIPPED, S.FOUND, S.EXPIRED)))
            return list((await s.scalars(q)).all())

    async def status_text(self, now: datetime | None = None) -> str:
        now = now or now_utc()
        async with self.sessionmaker() as s:
            counts = dict((await s.execute(select(Job.status, func.count()).group_by(Job.status))).all())
            calls = await self.llm_calls_today(s, now)
            drafts = await self.drafts_today(s, now)
            health = {r.key.split(":", 1)[1]: r.value for r in
                      (await s.scalars(select(KV).where(KV.key.like("freelance_health:%")))).all()}
        lines = ["🩺 <b>Frilans agenti holati</b>"]
        if not self.connectors:
            lines.append("• Hech qaysi platforma yoqilmagan (config.yaml → platforms)")
        tl = self.cfg.platforms.get("truelancer")
        if tl and tl.enabled and "truelancer" not in self.connectors:
            lines.append("• Truelancer: ⏸ bu qurilmada brauzer (Playwright) yo'q — masalan, telefonda o'chadi")
        if "email" not in self.connectors:
            lines.append("• Gmail (Upwork, Fiverr): ⏸ sozlanmagan — .env ga IMAP_USER va IMAP_APP_PASSWORD yozing")
        for name, conn in self.connectors.items():
            h = health.get(name, {})
            mode = ("faqat o'qish" if name == "email" else
                    "avtomatik yuborish" if conn.can_submit else "qo'lda yuborish (token yo'q)")
            if h.get("alerted"):
                state = f"❌ {h.get('last_error', '')[:120]}"
            elif h.get("last_ok"):
                state = f"✅ {fmt_local(datetime.fromisoformat(h['last_ok']))} ({h.get('count', 0)} ta loyiha)"
            else:
                state = "⏳ hali tekshirilmagan"
            lines.append(f"• {conn.title}: {state} · {mode}")
        nxt = self.next_run_at()
        lines.append(f"🔄 Keyingi qidiruv: {fmt_local(nxt) if nxt else '—'}{' (hozir qidiryapti…)' if self.running else ''}")
        lines.append(f"🤖 Bugun Gemini: {calls}/{self.cfg.max_llm_calls_per_day} · takliflar: "
                     f"{drafts}/{self.cfg.max_proposals_per_day}")
        lines.append("🗂 " + ", ".join(f"{st.value} {counts[st]}" for st in S if counts.get(st)))
        return "\n".join(lines)

    async def daily_summary(self, now: datetime | None = None) -> str:
        now = now or now_utc()
        since = self._local_midnight_utc(now)
        async with self.sessionmaker() as s:
            today = (await s.scalars(select(Job).where(Job.created_at >= since))).all()
            waiting = (await s.scalars(select(Job).where(Job.status == S.AWAITING_APPROVAL)
                                       .order_by(Job.fit.desc()).limit(5))).all()
        by = {}
        for j in today:
            by[j.status] = by.get(j.status, 0) + 1
        out = [f"🌙 <b>Frilans — kunlik xulosa</b> ({fmt_local(now, with_time=False)})",
               f"Topildi: {len(today)} · filtr/AI o'tkazdi: {by.get(S.SKIPPED, 0)} · "
               f"taklif tayyor: {by.get(S.AWAITING_APPROVAL, 0)} · tasdiqlandi: {by.get(S.APPROVED, 0)}"]
        if waiting:
            out.append("\n⏳ <b>Tasdiq kutayotganlar:</b>")
            for j in waiting:
                out.append(f"• #{j.id} fit {j.fit} · {html.escape(j.title[:70])} — /job {j.id}")
        return "\n".join(out)

    async def send_daily_summary(self) -> None:
        await self.sender.send(await self.daily_summary())

    async def _health(self, name: str, title: str, *, error: Exception | None = None, count: int = 0) -> None:
        async with self.sessionmaker() as s:
            row = await s.get(KV, f"freelance_health:{name}")
            if row is None:
                row = KV(key=f"freelance_health:{name}", value={})
                s.add(row)
            was = bool(row.value.get("alerted"))
            if error is None:
                row.value = {**row.value, "last_ok": now_utc().isoformat(), "count": count, "alerted": False}
            else:
                row.value = {**row.value, "last_error": f"{type(error).__name__}: {error}"[:300], "alerted": True}
            await s.commit()
        try:
            if error is not None and not was:
                await self.sender.send(f"⚠️ <b>{title}</b> ulanmadi: <code>{html.escape(str(error)[:200])}</code>\n"
                                       "Agent to'xtamaydi, keyingi safar qayta urinadi.")
            elif error is None and was:
                await self.sender.send(f"✅ <b>{title}</b> yana ishlayapti.")
        except Exception:
            log.exception("freelance.alert_failed")

    async def aclose(self) -> None:
        for c in self.connectors.values():
            await c.aclose()
