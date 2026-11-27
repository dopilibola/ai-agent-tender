"""Tender pipeline: scan sources → keyword filter → store → notify once → watch for changes → remind.

Read-only by design: nothing here submits bids, signs (E-IMZO) or messages a customer.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import KV, Lot, LotStatus, SeenLot
from app.tenders import cards
from app.tenders.catalog import KV_PREFIX, CatalogRow, classify
from app.tenders.filters import KeywordFilter, MatchResult, is_it_lot
from app.tenders.http import FileTooLarge
from app.tenders.schemas import LotFile, LotRecord, LotSummary
from app.tenders.sources.base import TenderSource
from app.tenders.timeutil import as_utc, fmt_local, now_utc, remaining_uz, subtract_workdays

log = logging.getLogger(__name__)

ACTIVE = (LotStatus.NEW, LotStatus.NOTIFIED, LotStatus.BIDDING)


class Sender(Protocol):
    async def send(self, text: str, reply_markup=None, **kw) -> Any: ...


class RateProvider(Protocol):
    async def get(self) -> float | None: ...


Analyzer = Callable[[Lot], Awaitable[None]]  # step 3 (Gemini) plugs in here


@dataclass
class ScanStats:
    listed: int = 0
    new_seen: int = 0
    details: int = 0
    new_lots: int = 0
    changed: int = 0
    closed: int = 0


def lot_dir_name(lot: Lot) -> str:
    """data/lots/<raqam>/ for etender (spec); other sources are prefixed to avoid id clashes."""
    return lot.external_id if lot.source == "etender" else f"{lot.source}-{lot.external_id}"


def fingerprint(lot: Lot) -> str:
    deadline = as_utc(lot.deadline_at)
    parts = [
        deadline.isoformat() if deadline else "",
        f"{lot.start_cost or 0:.2f}",
        lot.platform_status or "",
        f"{lot.pledge_percent or 0:g}",
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


class TenderService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker,
        sources: dict[str, TenderSource],
        sender: Sender,
        *,
        fx: RateProvider | None = None,
        analyzer: Analyzer | None = None,
    ) -> None:
        self.cfg = settings.config.tenders
        self.limit_usd = float(settings.company.zakalat_limiti_usd or 0)
        self.files_dir = settings.secrets.data_dir / "lots"
        self.sessionmaker = sessionmaker
        self.sources = sources
        self.sender = sender
        self.fx = fx
        self.analyzer = analyzer
        self.filter = KeywordFilter(self.cfg)
        self._scan_lock = asyncio.Lock()
        # Parsed «Barcha tenderlar» listing; only _save_catalog writes it, so that bumps the version.
        self._catalog_cache: tuple[list[CatalogRow], dict[str, datetime]] | None = None
        self.catalog_version = 0
        self.next_scan_at: Callable[[], datetime | None] = lambda: None
        self.prices = None  # PriceWatch (Uzum price check), set in main.py

    @property
    def scanning(self) -> bool:
        return self._scan_lock.locked()

    # ── Scan ────────────────────────────────────────────────────────────────
    async def run_scan(self, now: datetime | None = None) -> dict[str, ScanStats]:
        async with self._scan_lock:
            results: dict[str, ScanStats] = {}
            for name, source in self.sources.items():
                try:
                    stats = await self.scan_source(source, now)
                except Exception as e:
                    log.exception("tenders.scan_failed", extra={"source": name})
                    await self._health_fail(source, e)
                    continue
                results[name] = stats
                await self._health_ok(source, stats)
                log.info("tenders.scan_ok", extra={"source": name, **stats.__dict__})
            await self.send_pending(now)
            await self.analyze_backlog(now)
            return results

    async def analyze_backlog(self, now: datetime | None = None) -> dict[str, int]:
        """Quietly analyse lots announced before Gemini was on; one summary message at the end."""
        if not self.analyzer:
            return {}
        now = now or now_utc()
        limit = self.cfg.analysis.backfill_per_run
        async with self.sessionmaker() as s:
            ids = (
                await s.scalars(
                    select(Lot.id)
                    .where(Lot.status.in_((LotStatus.NOTIFIED, LotStatus.BIDDING)), Lot.analysis.is_(None))
                    .order_by(Lot.deadline_at)
                    .limit(limit)
                )
            ).all()
        counts: dict[str, int] = {}
        for lot_id in ids:
            async with self.sessionmaker() as s:
                lot = await s.get(Lot, lot_id)
                if lot is None or (lot.deadline_at and as_utc(lot.deadline_at) <= now):
                    continue
                try:
                    await self.analyzer(lot)
                except Exception:
                    log.exception("tenders.analyze_failed", extra={"lot": lot.external_id})
                    lot.analysis = {"error": "baholanmagan"}
                if not is_it_lot(lot) and lot.status == LotStatus.NOTIFIED:  # a lot you bid on stays yours
                    lot.status = LotStatus.CLOSED
                await s.commit()
                key = lot.verdict or "⚠️"
                counts[key] = counts.get(key, 0) + 1
        if counts:
            badges = {"QATNASHAMIZ": "✅", "SHARTLI": "🟡", "YO'Q": "❌", "⚠️": "⚠️ baholanmagan"}
            parts = " · ".join(f"{badges.get(k, k)} {v}" for k, v in counts.items())
            await self._safe_send(
                f"🤖 <b>Avval yuborilgan {sum(counts.values())} ta lot AI bilan tahlil qilindi:</b> {parts}\n"
                "Natijalar: /list · to'liq tahlil: /lot &lt;raqam&gt;"
            )
        return counts

    async def reanalyze(self, ref: str) -> tuple[Lot | None, str]:
        if not self.analyzer:
            return None, "AI tahlili o'chirilgan."
        async with self.sessionmaker() as s:
            lot = await self.find(s, ref)
            if lot is None:
                return None, "Bunday lot bazada yo'q. /list"
            await self.analyzer(lot)
            await s.commit()
        return await self.lot_text(ref)

    async def scan_source(self, source: TenderSource, now: datetime | None = None) -> ScanStats:
        now = now or now_utc()
        stats = ScanStats()
        rows = await source.list_open()
        listed = {r.external_id: r for r in rows}
        stats.listed = len(rows)
        try:
            await self._save_catalog(source.name, rows, now)
        except Exception:  # the «Barcha tenderlar» page must never break the IT pipeline
            log.exception("tenders.catalog_failed", extra={"source": source.name})
        rate = await self.fx.get() if self.fx else None

        async with self.sessionmaker() as s:
            seen_ids = set(
                await s.scalars(select(SeenLot.external_id).where(SeenLot.source == source.name))
            )
            for row in rows:
                if row.external_id in seen_ids:
                    continue
                m = self.filter.match(row.title)
                hard_excluded = bool(m.excluded_by and not m.override)
                scan_all = self.cfg.scan_descriptions and getattr(source, "scan_descriptions", True)
                needs = getattr(source, "needs_detail", None)  # phone sources: only cards that can change the answer
                if scan_all and needs is not None:
                    scan_all = needs(row)
                skip_detail = hard_excluded or (not m.is_it and not scan_all)
                s.add(
                    SeenLot(
                        source=source.name,
                        external_id=row.external_id,
                        title=row.title[:1000],
                        name_matched=m.is_it,
                        detail_checked=skip_detail,
                    )
                )
                stats.new_seen += 1
            await s.commit()

            pending = (
                await s.scalars(
                    select(SeenLot)
                    .where(SeenLot.source == source.name, SeenLot.detail_checked.is_(False))
                    .order_by(SeenLot.name_matched.desc(), SeenLot.id)
                )
            ).all()

        budget = min(self.cfg.max_detail_fetches_per_run, getattr(source, "max_details", 10**6))
        fails = 0
        for seen in pending:
            summary = listed.get(seen.external_id)
            if summary is None:  # no longer open — nothing to check
                await self._mark_checked(seen.id)
                continue
            needs = getattr(source, "needs_detail", None)
            if needs is not None and not seen.name_matched and not needs(summary):
                await self._mark_checked(seen.id)  # queued before needs_detail existed: the name says enough
                continue
            if budget <= 0:
                if needs is None:
                    break
                continue  # keep clearing the cards that need no visit; the rest wait for the next scan
            budget -= 1
            try:
                record = await source.get_lot(seen.external_id)
            except Exception as e:
                # One slow or broken card must not stop the rest: it stays unchecked and is tried next scan.
                # Three in a row means the site itself is down → the source is reported as failing.
                fails += 1
                log.warning("tenders.detail_failed",
                            extra={"source": source.name, "lot": seen.external_id, "err": str(e)[:200]})
                if fails >= 3:
                    raise
                continue
            fails = 0
            stats.details += 1
            m = self.filter.match(summary.title, record.title, record.description)
            if m.is_it and record.is_open:
                await self._create_lot(source, summary, record, m, rate)
                stats.new_lots += 1
            await self._mark_checked(seen.id)

        changed, closed = await self._watch_changes(source, listed, now, rate)
        stats.changed, stats.closed = changed, closed
        return stats

    # ── «Barcha tenderlar» catalog (every open lot, goods / services / IT) ────
    async def _save_catalog(self, name: str, rows: list[LotSummary], now: datetime) -> None:
        data = [CatalogRow.from_summary(r, classify(r, self.filter)).to_json() for r in rows]
        async with self.sessionmaker() as s:
            row = await s.get(KV, KV_PREFIX + name)
            if row is None:
                row = KV(key=KV_PREFIX + name, value={})
                s.add(row)
            row.value = {"at": now.isoformat(), "rows": data}
            await s.commit()
        self._catalog_cache = None
        self.catalog_version += 1
        counts: dict[str, int] = {}
        for d in data:
            counts[d["k"]] = counts.get(d["k"], 0) + 1
        log.info("tenders.catalog_saved", extra={"source": name, **counts})

    async def catalog(self) -> tuple[list[CatalogRow], dict[str, datetime]]:
        """Latest open-lot listing of every source + when each was fetched.

        Parsing ~4000 rows (and classifying their yo'nalish) took seconds on the phone server, so the parsed
        listing is kept in memory until the next scan saves a new one. Callers get their own row copies,
        because the site fills per-request fields (usd, zakalat, AI verdict) on them."""
        if self._catalog_cache is None:
            async with self.sessionmaker() as s:
                kvs = (await s.scalars(select(KV).where(KV.key.like(KV_PREFIX + "%")))).all()
            out: list[CatalogRow] = []
            at: dict[str, datetime] = {}
            for kv in kvs:
                name = kv.key[len(KV_PREFIX):]
                if name not in self.sources:
                    continue
                value = kv.value or {}
                if value.get("at"):
                    at[name] = datetime.fromisoformat(value["at"])
                out.extend(CatalogRow.from_json(d) for d in value.get("rows") or [])
            self._catalog_cache = (out, at)
        rows, at = self._catalog_cache
        return [copy.copy(r) for r in rows], dict(at)

    async def _mark_checked(self, seen_id: int) -> None:
        async with self.sessionmaker() as s:
            seen = await s.get(SeenLot, seen_id)
            if seen:
                seen.detail_checked = True
                await s.commit()

    async def _create_lot(
        self, source: TenderSource, summary: LotSummary, rec: LotRecord, m: MatchResult, rate: float | None
    ) -> None:
        async with self.sessionmaker() as s:
            exists = await s.scalar(
                select(Lot).where(Lot.source == source.name, Lot.external_id == rec.external_id)
            )
            if exists:
                return
            lot = Lot(source=source.name, external_id=rec.external_id, status=LotStatus.NEW)
            self._apply(lot, rec, summary)
            lot.matched_keywords = m.keywords
            lot.usd_rate = rate
            lot.fingerprint = fingerprint(lot)
            s.add(lot)
            await s.commit()
            lot_id = lot.id
        log.info("tenders.new_lot", extra={"source": source.name, "lot": rec.external_id})
        if self.cfg.download_files and rec.files:
            await self._download_files(source, lot_id)

    @staticmethod
    def _apply(lot: Lot, rec: LotRecord, summary: LotSummary | None = None) -> None:
        lot.url = rec.url
        use_summary = summary and summary.title and not summary.title_is_category
        lot.title = (summary.title if use_summary else rec.title)[:2000]
        lot.description = rec.description
        lot.lot_type = rec.lot_type or (summary.lot_type if summary else None)
        lot.customer_name = rec.customer_name or (summary.customer_name if summary else None)
        lot.customer_tin = rec.customer_tin or (summary.customer_tin if summary else None)
        lot.customer_region = rec.customer_region or (summary.customer_region if summary else None)
        lot.start_cost = rec.start_cost
        lot.currency = rec.currency
        lot.pledge_percent = rec.pledge_percent
        lot.pledge_amount = rec.pledge_amount
        lot.advance_percent = rec.advance_percent
        lot.payment_days = rec.payment_days
        lot.execution_days = rec.execution_days
        lot.warranty = rec.warranty
        lot.published_at = rec.published_at
        lot.deadline_at = rec.deadline_at
        lot.valuation_method = rec.valuation_method
        lot.price_weight = rec.price_weight
        lot.tech_weight = rec.tech_weight
        lot.pass_mark = rec.pass_mark
        lot.criteria = [c.model_dump() for c in rec.criteria]
        lot.qualification = list(rec.qualification)
        lot.languages = list(rec.languages)
        lot.financing_source = rec.financing_source
        old_local = {f.get("path"): f.get("local") for f in (lot.files or [])}
        lot.files = [{**f.model_dump(), "local": old_local.get(f.path)} for f in rec.files]
        lot.platform_status = rec.platform_status
        lot.is_prolonged = rec.is_prolonged
        lot.raw = rec.raw

    def lot_dir(self, lot: Lot) -> Path:
        return self.files_dir / lot_dir_name(lot)

    async def _download_files(self, source: TenderSource, lot_id: int) -> None:
        async with self.sessionmaker() as s:
            lot = await s.get(Lot, lot_id)
            if lot is None:
                return
            files = [dict(f) for f in lot.files or []]
            for f in files:
                if f.get("local") and Path(f["local"]).exists():  # noqa: ASYNC240 - tiny stat
                    continue
                ext = (f.get("ext") or Path(f.get("name") or f["path"]).suffix.lstrip(".") or "bin").lower()
                dest = self.lot_dir(lot) / f"{f['kind']}.{ext}"
                try:
                    await source.download(
                        LotFile.model_validate(f), dest, max_bytes=int(self.cfg.max_file_mb * 1024 * 1024)
                    )
                    f["local"] = str(dest)
                except FileTooLarge:
                    log.warning("tenders.file_too_large", extra={"lot": lot.external_id, "kind": f["kind"]})
                except Exception as e:
                    log.warning("tenders.download_failed",
                                extra={"lot": lot.external_id, "kind": f["kind"], "error": str(e)[:200]})
            lot.files = files
            await s.commit()

    # ── Changes ─────────────────────────────────────────────────────────────
    async def _watch_changes(
        self, source: TenderSource, listed: dict[str, LotSummary], now: datetime, rate: float | None
    ) -> tuple[int, int]:
        changed = closed = 0
        async with self.sessionmaker() as s:
            lots = (
                await s.scalars(select(Lot).where(Lot.source == source.name, Lot.status.in_(ACTIVE)))
            ).all()
            for lot in lots:
                summary = listed.get(lot.external_id)
                deadline = as_utc(lot.deadline_at)
                if summary is None:
                    if deadline and deadline <= now:
                        await self._close(s, lot, None)
                        closed += 1
                        continue
                    rec = await source.get_lot(lot.external_id)
                    if not rec.is_open:
                        old = lot.platform_status
                        self._apply(lot, rec)
                        await self._close(s, lot, [f"Holat: {old or '?'} → {rec.platform_status or '?'}"])
                        closed += 1
                    continue

                diffs = self._summary_diffs(lot, summary)
                if not diffs:
                    continue
                rec = await source.get_lot(lot.external_id)
                old_fp, old_deadline = lot.fingerprint, deadline
                old_pledge = lot.pledge_percent
                self._apply(lot, rec, summary)
                if rate and not lot.usd_rate:
                    lot.usd_rate = rate
                if old_pledge != lot.pledge_percent:
                    diffs.append(f"Zakalat: {old_pledge or 0:g}% → {lot.pledge_percent or 0:g}%")
                lot.fingerprint = fingerprint(lot)
                if lot.fingerprint == old_fp:
                    continue
                new_deadline = as_utc(lot.deadline_at)
                if lot.status == LotStatus.BIDDING and new_deadline and old_deadline and new_deadline > old_deadline:
                    lot.reminders_sent = []  # deadline extended → remind again
                await s.commit()
                changed += 1
                if lot.status in (LotStatus.NOTIFIED, LotStatus.BIDDING):
                    await self._send(cards.change_message(lot, diffs), cards.lot_keyboard(lot))
        return changed, closed

    @staticmethod
    def _summary_diffs(lot: Lot, summary: LotSummary) -> list[str]:
        diffs = []
        old, new = as_utc(lot.deadline_at), as_utc(summary.deadline_at)
        if old and new and abs((new - old).total_seconds()) > 60:
            word = "uzaytirildi" if new > old else "qisqartirildi"
            diffs.append(f"Topshirish muddati {word}: {fmt_local(old)} → {fmt_local(new)}")
        if summary.start_cost is not None and lot.start_cost is not None and abs(summary.start_cost - lot.start_cost) > 1:
            diffs.append(
                f"Boshlang'ich narx: {cards.money_uzs(lot.start_cost, lot.currency)} → "
                f"{cards.money_uzs(summary.start_cost, summary.currency)}"
            )
        return diffs

    async def _close(self, s: AsyncSession, lot: Lot, changes: list[str] | None) -> None:
        was = lot.status
        lot.status = LotStatus.CLOSED
        await s.commit()
        if was == LotStatus.NEW:
            return  # never announced → close silently
        if changes:
            await self._send(cards.change_message(lot, ["❌ Lot yopildi yoki bekor qilindi", *changes]))
        elif was == LotStatus.BIDDING:
            await self._send(cards.change_message(lot, ["⌛ Topshirish muddati tugadi"]))

    # ── Notifications ───────────────────────────────────────────────────────
    async def _send(self, text: str, markup=None) -> None:
        await self.sender.send(text, reply_markup=markup)

    async def deposit_used_usd(self, s: AsyncSession, exclude_id: int | None = None) -> float:
        lots = (await s.scalars(select(Lot).where(Lot.status == LotStatus.BIDDING))).all()
        return sum(cards.pledge_usd(lot) or 0 for lot in lots if lot.id != exclude_id)

    async def send_pending(self, now: datetime | None = None) -> int:
        """Announce every NEW lot exactly once (retried next run if Telegram fails)."""
        now = now or now_utc()
        sent = 0
        async with self.sessionmaker() as s:
            ids = (await s.scalars(select(Lot.id).where(Lot.status == LotStatus.NEW).order_by(Lot.id))).all()
        for lot_id in ids:
            async with self.sessionmaker() as s:
                lot = await s.get(Lot, lot_id)
                if lot is None or lot.status != LotStatus.NEW:
                    continue
                deadline = as_utc(lot.deadline_at)
                if deadline and deadline <= now:
                    lot.status = LotStatus.CLOSED
                    await s.commit()
                    continue
                if self.analyzer and lot.analysis is None:
                    try:
                        await self.analyzer(lot)
                    except Exception:
                        log.exception("tenders.analyze_failed", extra={"lot": lot.external_id})
                        lot.analysis = {"error": "baholanmagan"}
                    await s.commit()
                if not is_it_lot(lot):
                    # Gemini read the documents: not an IT service (hardware, construction, ecology…) →
                    # closed at once, never shown on the site, in /list or in the daily report.
                    lot.status = LotStatus.CLOSED
                    await s.commit()
                    log.info("tenders.not_it_closed", extra={"lot": lot.external_id, "source": lot.source})
                    continue
                # Every IT lot is announced, "YO'Q" too (27.11.2026: the owner wants to see them all);
                # the card shows the verdict and its reason.
                used = await self.deposit_used_usd(s, exclude_id=lot.id)
                try:
                    await self._send(
                        cards.lot_card(lot, used_usd=used, limit_usd=self.limit_usd, now=now),
                        cards.lot_keyboard(lot),
                    )
                except Exception:
                    log.exception("tenders.notify_failed", extra={"lot": lot.external_id})
                    return sent
                sent += 1
                lot.status = LotStatus.NOTIFIED
                lot.notified_at = now
                await s.commit()
        return sent

    # ── Reminders (bidding lots) ────────────────────────────────────────────
    def due_reminders(self, lot: Lot, now: datetime) -> list[str]:
        deadline = as_utc(lot.deadline_at)
        if not deadline or deadline <= now:
            return []
        due = []
        questions_at = deadline - timedelta(days=self.cfg.questions_deadline_days_before)
        if questions_at - timedelta(days=1) <= now < questions_at:
            due.append("questions")
        if subtract_workdays(deadline, self.cfg.deposit_reminder_workdays) <= now:
            due.append("deposit")
        for hours in sorted(self.cfg.deadline_reminders_hours, reverse=True):
            if deadline - timedelta(hours=hours) <= now:
                due.append(f"h{hours}")
        return [k for k in due if k not in (lot.reminders_sent or [])]

    async def run_reminders(self, now: datetime | None = None) -> int:
        now = now or now_utc()
        sent = 0
        async with self.sessionmaker() as s:
            lots = (await s.scalars(select(Lot).where(Lot.status == LotStatus.BIDDING))).all()
            for lot in lots:
                deadline = as_utc(lot.deadline_at)
                if deadline and deadline <= now:
                    await self._close(s, lot, None)
                    continue
                keys = self.due_reminders(lot, now)
                if not keys:
                    continue
                lines = [f"🔔 <b>Eslatma</b> — 📌 <b>{cards.esc(lot.external_id)}</b> "
                         f"{cards.esc(cards.short(lot.title, 90))}"]
                if "questions" in keys:
                    q_at = deadline - timedelta(days=self.cfg.questions_deadline_days_before)
                    lines.append(f"❓ Savol berish muddati (taxminan {fmt_local(q_at)}) yaqin — "
                                 "savollarni lot chatiga yuboring.")
                if "deposit" in keys:
                    pledge = (f" ({cards.money_uzs(lot.pledge_amount, lot.currency)})"
                              if lot.pledge_amount else "")
                    lines.append(f"💳 Zakalatni o'tkazing{pledge}: topshirishgacha "
                                 f"{self.cfg.deposit_reminder_workdays} ish kuni yoki kamroq qoldi.")
                if any(k.startswith("h") for k in keys):
                    lines.append(f"⏰ Topshirishga {remaining_uz(deadline, now)}! ({fmt_local(deadline)})")
                lines.append(f"🔗 {cards.esc(lot.url)}")
                await self._send("\n".join(lines))
                lot.reminders_sent = [*(lot.reminders_sent or []), *keys]
                await s.commit()
                sent += 1
        return sent

    # ── Commands / report ───────────────────────────────────────────────────
    async def find(self, s: AsyncSession, ref: str) -> Lot | None:
        ref = ref.strip().lstrip("#")
        return await s.scalar(select(Lot).where(Lot.external_id == ref).order_by(Lot.id.desc()))

    async def set_status(self, ref: str | int, status: LotStatus) -> Lot | None:
        async with self.sessionmaker() as s:
            lot = await s.get(Lot, ref) if isinstance(ref, int) else await self.find(s, ref)
            if lot is None:
                return None
            lot.status = status
            if status == LotStatus.BIDDING:
                lot.reminders_sent = []
            await s.commit()
            return lot

    async def open_lots(self, now: datetime | None = None, *, bidding_only: bool = False) -> list[Lot]:
        now = now or now_utc()
        statuses = (LotStatus.BIDDING,) if bidding_only else ACTIVE
        async with self.sessionmaker() as s:
            lots = (
                await s.scalars(select(Lot).where(Lot.status.in_(statuses)).order_by(Lot.deadline_at))
            ).all()
        return [lot for lot in lots
                if (not lot.deadline_at or as_utc(lot.deadline_at) > now) and is_it_lot(lot, self.filter)]

    async def lot_text(self, ref: str | int, *, full: bool = True) -> tuple[Lot | None, str]:
        async with self.sessionmaker() as s:
            lot = await s.get(Lot, ref) if isinstance(ref, int) else await self.find(s, ref)
            if lot is None:
                return None, "Bunday lot bazada yo'q. /list — ochiq IT lotlar."
            used = await self.deposit_used_usd(s, exclude_id=lot.id)
            render = cards.lot_full if full else cards.lot_card
            return lot, render(lot, used_usd=used, limit_usd=self.limit_usd)

    async def health(self) -> dict[str, dict]:
        async with self.sessionmaker() as s:
            rows = (await s.scalars(select(KV).where(KV.key.like("tender_health:%")))).all()
            return {r.key.split(":", 1)[1]: r.value for r in rows}

    async def status_text(self, now: datetime | None = None) -> str:
        now = now or now_utc()
        health = await self.health()
        async with self.sessionmaker() as s:
            counts = dict((await s.execute(select(Lot.status, func.count()).group_by(Lot.status))).all())
            seen = await s.scalar(select(func.count()).select_from(SeenLot)) or 0
            backlog = await s.scalar(
                select(func.count()).select_from(SeenLot).where(SeenLot.detail_checked.is_(False))
            ) or 0
        lines = ["🩺 <b>Tender agenti holati</b>"]
        for name, src in self.sources.items():
            h = health.get(name, {})
            if h.get("alerted"):
                state = f"❌ xato: {cards.esc(cards.short(h.get('last_error'), 150))}"
            elif h.get("last_ok"):
                state = f"✅ {fmt_local(datetime.fromisoformat(h['last_ok']))} ({h.get('listed', 0)} ta ochiq lot)"
            else:
                state = "⏳ hali tekshirilmagan"
            lines.append(f"• {cards.esc(src.title)}: {state}")
        nxt = self.next_scan_at()
        lines.append(f"🔄 Keyingi tekshiruv: {fmt_local(nxt) if nxt else '—'}"
                     f"{' (hozir tekshiryapti…)' if self.scanning else ''}")
        lines.append(
            "🗂 IT lotlar: "
            + ", ".join(f"{cards.STATUS_UZ[st]} {counts.get(st, 0)}" for st in LotStatus)
        )
        lines.append(f"👁 Ko'rilgan lotlar: {seen} (tavsifi tekshirilishi kutilayotgan: {backlog})")
        return "\n".join(lines)

    async def daily_report(self, now: datetime | None = None) -> str:
        now = now or now_utc()
        since = now - timedelta(hours=24)
        soon = now + timedelta(days=3)
        async with self.sessionmaker() as s:
            fresh = (
                await s.scalars(select(Lot).where(Lot.first_seen_at >= since).order_by(Lot.deadline_at))
            ).all()
        open_lots = await self.open_lots(now)
        fresh = [lot for lot in fresh if is_it_lot(lot)]  # non-IT lots are not reported at all
        good = [lot for lot in fresh if lot.verdict != "YO'Q"]
        bad = [lot for lot in fresh if lot.verdict == "YO'Q"]
        approaching = [
            lot for lot in open_lots
            if lot.status in (LotStatus.NOTIFIED, LotStatus.BIDDING) and as_utc(lot.deadline_at) <= soon
        ]
        bidding = [lot for lot in open_lots if lot.status == LotStatus.BIDDING]

        out = [f"📊 <b>Tenderlar — kunlik hisobot</b> ({fmt_local(now, with_time=False)})", ""]
        out.append(f"🆕 <b>Yangi IT lotlar (24 soat): {len(good)}</b>")
        out.extend(cards.one_line(lot, now) for lot in good[:15])
        if len(good) > 15:
            out.append(f"… va yana {len(good) - 15} ta (/list)")
        if bad:
            out.append("❌ IT, lekin mos emas: " + ", ".join(cards.lot_link(lot) for lot in bad))
        out.append("")
        out.append(f"⏰ <b>3 kun ichida muddati tugaydi: {len(approaching)}</b>")
        out.extend(cards.one_line(lot, now) for lot in approaching[:10])
        out.append("")
        out.append(f"📌 <b>Qatnashayotganlar: {len(bidding)}</b>")
        out.extend(cards.one_line(lot, now) for lot in bidding)
        health = await self.health()
        bad_sources = [n for n, h in health.items() if h.get("alerted")]
        out.append("")
        out.append("🩺 Manbalar: " + ("✅ hammasi ishlayapti" if not bad_sources
                                     else "❌ ishlamayapti: " + ", ".join(bad_sources)))
        return "\n".join(out)

    async def send_daily_report(self) -> None:
        await self._send(await self.daily_report())

    # ── Source health (alert once per outage) ───────────────────────────────
    async def _health(self, s: AsyncSession, name: str) -> KV:
        row = await s.get(KV, f"tender_health:{name}")
        if row is None:
            row = KV(key=f"tender_health:{name}", value={})
            s.add(row)
        return row

    async def _health_ok(self, source: TenderSource, stats: ScanStats) -> None:
        async with self.sessionmaker() as s:
            row = await self._health(s, source.name)
            was_alerted = bool(row.value.get("alerted"))
            row.value = {**row.value, "last_ok": now_utc().isoformat(), "listed": stats.listed, "alerted": False}
            await s.commit()
        if was_alerted:
            await self._safe_send(f"✅ <b>{cards.esc(source.title)}</b> yana ishlayapti.")

    async def _health_fail(self, source: TenderSource, error: Exception) -> None:
        msg = f"{type(error).__name__}: {error}"[:500]
        async with self.sessionmaker() as s:
            row = await self._health(s, source.name)
            first = not row.value.get("alerted")
            row.value = {**row.value, "last_error": msg, "error_at": now_utc().isoformat(), "alerted": True}
            await s.commit()
        if first:
            await self._safe_send(
                f"⚠️ <b>{cards.esc(source.title)}</b> manbasi ishlamayapti.\n"
                f"<code>{cards.esc(msg[:300])}</code>\n"
                "Agent to'xtamaydi — keyingi tekshiruvda qayta urinadi. Tiklanganda xabar beraman."
            )

    async def _safe_send(self, text: str) -> None:
        try:
            await self._send(text)
        except Exception:
            log.exception("tenders.alert_failed")

    async def aclose(self) -> None:
        for source in self.sources.values():
            await source.aclose()
