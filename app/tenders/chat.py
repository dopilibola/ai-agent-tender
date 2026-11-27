"""🤖 Lot chat: ask Gemini questions about one lot — any lot link of the five sources, not only IT lots.

Opening a lot (slow: phone sources take ~30–60 s) runs in the background:
1. the link is turned into (source, id) — only the five known sites, so no arbitrary URL is ever fetched;
2. the lot card is read with the source's own `get_lot` (HTTP or the phone's Chrome); a lot the bot already
   keeps (IT lots) brings its downloaded files and its earlier AI analysis;
3. attachments are downloaded where the source allows it (etender, xarid) and turned into text by
   app/tenders/documents.py; one scanned PDF is transcribed once by Gemini;
4. the context (lot facts, items, documents, company profile) is stored in KV `lotchat:<source>-<id>`
   with the conversation, so every question sends the same context plus the last turns.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from sqlalchemy import func, select

from app.db.models import KV, LLMCall, Lot
from app.llm.prompts import load_prompt
from app.llm.safety import new_tag, untrusted_rule, wrap_untrusted
from app.tenders import cards
from app.tenders.documents import DocText, extract_lot_documents
from app.tenders.timeutil import TASHKENT, as_utc, fmt_local, now_utc

log = logging.getLogger(__name__)

KV_PREFIX = "lotchat:"
PURPOSE = "lot_chat"
MAX_QUESTION = 2000
HISTORY_TURNS = 8  # earlier Q/A pairs sent with a new question
OPEN_TIMEOUT_S = 240

SOURCE_TITLE = {"etender": "etender.uzex.uz", "xarid": "xarid.uzex.uz (taklif so'rovi)",
                "xarid_tanlov": "xarid.uzex.uz (tanlov)", "xarid_auction": "xarid.uzex.uz (auksion)",
                "hayotbirja": "xt-xarid.uz (Hayot birja)"}
_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("etender.uzex.uz", "etender", re.compile(r"^/lot/(\d+)")),
    ("xarid.uzex.uz", "xarid", re.compile(r"^/request-proposal/\d+/detail/(\d+)")),
    ("xarid.uzex.uz", "xarid_tanlov", re.compile(r"^/purchase/competition/detail/(\d+)")),
    ("xarid.uzex.uz", "xarid_auction", re.compile(r"^/auction/detail/(\d+)")),
    ("xt-xarid.uz", "hayotbirja", re.compile(r"^/procedure/(\d+)")),
    ("hayotbirja.uz", "hayotbirja", re.compile(r"^/procedure/(\d+)")),
]
KEY_RE = re.compile(r"^(etender|xarid|xarid_tanlov|xarid_auction|hayotbirja)-(\d{1,15})$")
SUGGESTIONS = [
    "Bu lot nima haqida? Qisqacha tushuntir.",
    "Ijrochiga qanday talablar qo'yilgan?",
    "Zakalat, avans va to'lov shartlari qanday?",
    "Muddatlar: qachongacha ariza beriladi, ish qancha vaqtda bajariladi?",
    "Bizning kompaniya qatnasha oladimi? Nima yetishmaydi?",
    "Arizaga qanday hujjatlar tayyorlash kerak?",
]


class ChatError(ValueError):
    """A message for the person: bad link, unknown source, lot not found…"""


def parse_ref(ref: str) -> tuple[str, str]:
    """A lot link of the five sites, or our own key ("etender-525977") → (source, id)."""
    ref = (ref or "").strip()
    if m := KEY_RE.match(ref):
        return m.group(1), m.group(2)
    if not re.match(r"^https?://", ref):
        ref = "https://" + ref
    parts = urlsplit(ref)
    host = (parts.hostname or "").lower().removeprefix("www.")
    for site, source, rx in _PATTERNS:
        if host == site and (m := rx.match(parts.path)):
            return source, m.group(1)
    raise ChatError("Havola tanilmadi. etender.uzex.uz/lot/…, xarid.uzex.uz (taklif so'rovi, tanlov, auksion) "
                    "yoki xt-xarid.uz/procedure/… lot havolasini qo'ying.")


def chat_key(source: str, lot_id: str) -> str:
    return f"{source}-{lot_id}"


def _now() -> str:
    return now_utc().isoformat()


class LotChat:
    def __init__(self, gemini, settings, sessionmaker, tenders):
        self.gemini = gemini
        self.settings = settings
        self.cfg = settings.config.tenders.chat
        self.acfg = settings.config.tenders.analysis
        self.company = settings.company.model_dump()
        self.limit_usd = float(settings.company.zakalat_limiti_usd or 0)
        self.sessionmaker = sessionmaker
        self.tenders = tenders
        self.root = settings.secrets.data_dir / "lot_chat"
        self.prompt = load_prompt("lot_chat")
        self._tasks: dict[str, asyncio.Task] = {}
        self._ask_lock: dict[str, asyncio.Lock] = {}

    # ── storage ─────────────────────────────────────────────────────────────
    async def get(self, key: str) -> dict | None:
        async with self.sessionmaker() as s:
            kv = await s.get(KV, KV_PREFIX + key)
            return dict(kv.value) if kv and kv.value else None

    async def _put(self, key: str, value: dict) -> None:
        value["at"] = _now()
        async with self.sessionmaker() as s:
            kv = await s.get(KV, KV_PREFIX + key)
            if kv is None:
                s.add(KV(key=KV_PREFIX + key, value=value))
            else:
                kv.value = value
            await s.commit()

    async def recent(self, limit: int = 30) -> list[dict]:
        async with self.sessionmaker() as s:
            kvs = (await s.scalars(select(KV).where(KV.key.like(KV_PREFIX + "%")))).all()
        rows = [dict(kv.value or {}, key=kv.key[len(KV_PREFIX):]) for kv in kvs]
        rows.sort(key=lambda v: v.get("at", ""), reverse=True)
        return rows[:limit]

    async def delete(self, key: str) -> None:
        async with self.sessionmaker() as s:
            kv = await s.get(KV, KV_PREFIX + key)
            if kv is not None:
                await s.delete(kv)
                await s.commit()

    # ── opening a lot ───────────────────────────────────────────────────────
    async def open(self, ref: str, *, fresh: bool = False) -> str:
        """Start reading the lot (in the background) and return its chat key at once."""
        source, lot_id = parse_ref(ref)
        sources = self.tenders.sources if self.tenders else {}
        if source not in sources:
            raise ChatError(f"{SOURCE_TITLE.get(source, source)} manbasi hozir o'chirilgan.")
        key = chat_key(source, lot_id)
        old = await self.get(key)
        if old and old.get("status") == "ready" and not fresh:
            return key
        running = self._tasks.get(key)
        if running and not running.done():
            return key
        await self._put(key, {"source": source, "id": lot_id, "status": "loading", "title": (old or {}).get("title")
                              or f"{SOURCE_TITLE[source]} · lot {lot_id}", "messages": (old or {}).get("messages", []),
                              "started": _now()})
        self._tasks[key] = asyncio.create_task(self._load(key, source, lot_id))
        return key

    async def resume(self, key: str, data: dict) -> dict:
        """A chat left «loading» by a restart has no task any more: start reading again."""
        task = self._tasks.get(key)
        if data.get("status") == "loading" and (task is None or task.done()):
            await self.open(key, fresh=True)
            return await self.get(key) or data
        return data

    async def _load(self, key: str, source: str, lot_id: str) -> None:
        try:
            ctx = await asyncio.wait_for(self._build(source, lot_id), OPEN_TIMEOUT_S)
        except Exception as e:
            msg = str(e) if isinstance(e, ChatError) else f"{type(e).__name__}: {e}"
            log.warning("chat.open_failed", extra={"lot": key, "err": msg[:300]})
            cur = await self.get(key) or {}
            cur.update(status="error", error=msg[:400])
            await self._put(key, cur)
            return
        cur = await self.get(key) or {}
        cur.update(status="ready", error=None, **ctx)
        await self._put(key, cur)
        log.info("chat.opened", extra={"lot": key, "docs": len(ctx["docs"]), "chars": len(ctx["context"])})

    async def _build(self, source: str, lot_id: str) -> dict[str, Any]:
        from app.tenders.prices import items_from_record
        from app.tenders.service import TenderService

        src = self.tenders.sources[source]
        async with self.sessionmaker() as s:
            stored = await s.scalar(select(Lot).where(Lot.source == source, Lot.external_id == lot_id))
        rec = await src.get_lot(lot_id)
        lot = stored or Lot(source=source, external_id=lot_id)
        if stored is None:
            TenderService._apply(lot, rec, None)
            lot.files = [f.model_dump() for f in rec.files]
        self._fill_from_catalog(lot, await self._catalog_row(source, lot_id))
        items = items_from_record(rec)
        files = [dict(f) for f in lot.files or []]
        workdir = self.root / chat_key(source, lot_id)
        if files and getattr(src, "download", None):
            files = await self._download(src, files, workdir)
        docs = await asyncio.to_thread(
            extract_lot_documents, files, workdir / "x", max_chars=self.acfg.max_doc_tokens * 4,
            scan_pdf_max_bytes=int(self.acfg.max_scan_pdf_mb * 1024 * 1024),
            scan_pdf_max_pages=self.acfg.max_scan_pdf_pages)
        await self._transcribe_scans(docs)
        from app.tenders.analyzer import lot_facts

        facts = lot_facts(lot)
        facts["manba"] = SOURCE_TITLE.get(source, source)
        facts["holat"] = rec.platform_status
        sections = ["## LOT SAHIFASI (sayt ma'lumoti)", json.dumps(facts, ensure_ascii=False, indent=1)]
        if items:
            sections += ["", "## POZITSIYALAR (nomi — miqdori — birlik narxi)"]
            sections += [f"- {i.name} — {i.qty or '?'} {i.unit} — {i.price:,.0f}".replace(",", " ") for i in items[:200]]
        analysis = (stored.analysis if stored else None) or {}
        if analysis.get("summary_uz"):
            sections += ["", "## AVVALGI AI TAHLILI (bot o'zi qilgan)", json.dumps(
                {k: analysis.get(k) for k in ("summary_uz", "verdict", "verdict_reason_uz", "fit_score",
                                               "win_chance_percent", "blockers", "risks", "key_dates")
                 if analysis.get(k)}, ensure_ascii=False, indent=1)]
        sections += ["", "## HUJJATLAR"]
        doc_list = []
        for d in docs:
            head = f"[{d.label}] {d.filename}" + (f" — {d.note}" if d.note else "")
            if d.text:
                sections += [f"### {head}", d.text]
            else:
                sections.append(f"### {head} — o'qilmadi")
            doc_list.append({"label": d.label, "file": d.filename, "chars": len(d.text), "note": d.note})
        if not docs:
            sections.append("Hujjatlar yo'q yoki bu sayt fayllarni botga bermaydi — faqat lot sahifasi bor.")
        return {
            "title": lot.title, "url": lot.url, "customer": lot.customer_name, "region": lot.customer_region,
            "cost": lot.start_cost, "currency": lot.currency, "deadline": lot.deadline_at.isoformat()
            if lot.deadline_at else None, "lot_type": lot.lot_type, "status_text": rec.platform_status,
            "items": len(items), "docs": doc_list, "context": "\n".join(sections),
        }

    async def _catalog_row(self, source: str, lot_id: str):
        if not self.tenders:
            return None
        rows, _ = await self.tenders.catalog()
        return next((r for r in rows if r.source == source and r.external_id == lot_id), None)

    @staticmethod
    def _fill_from_catalog(lot: Lot, row) -> None:
        """The list row knows a few things a phone-read card may not (deadline, cost, region)."""
        if row is None:
            return
        lot.deadline_at = lot.deadline_at or row.deadline_at
        lot.start_cost = lot.start_cost or row.start_cost
        lot.currency = lot.currency or row.currency
        lot.customer_name = lot.customer_name or row.customer_name
        lot.customer_region = lot.customer_region or row.customer_region
        lot.lot_type = lot.lot_type or row.lot_type

    async def _download(self, src, files: list[dict], workdir: Path) -> list[dict]:
        from app.tenders.schemas import LotFile

        max_bytes = int(self.settings.config.tenders.max_file_mb * 1024 * 1024)
        for f in files:
            if f.get("local") and Path(f["local"]).exists():  # noqa: ASYNC240 - tiny stat
                continue
            ext = (f.get("ext") or Path(f.get("name") or f["path"]).suffix.lstrip(".") or "bin").lower()
            dest = workdir / f"{f['kind']}-{hashlib.sha1(str(f['path']).encode()).hexdigest()[:10]}.{ext}"
            if dest.exists():  # noqa: ASYNC240 - tiny stat
                f["local"] = str(dest)
                continue
            try:
                await src.download(LotFile.model_validate(f), dest, max_bytes=max_bytes)
                f["local"] = str(dest)
            except Exception as e:  # a file that will not come is reported in the docs list
                log.warning("chat.download_failed", extra={"file": f.get("name"), "err": str(e)[:200]})
        return files

    async def _transcribe_scans(self, docs: list[DocText]) -> None:
        """A scanned PDF has no text layer: Gemini reads it once, its text then stays in the context."""
        for d in docs:
            if d.mode != "pdf_file" or not d.path:
                continue
            if not await self._budget_ok():
                d.mode, d.note = "skipped", "kunlik AI limiti tugadi — skaner o'qilmadi"
                continue
            uploaded = None
            try:
                uploaded = await self.gemini.upload_file(str(d.path), "application/pdf")
                d.text = await self.gemini.generate_text(
                    ["Bu skanerlangan tender hujjati. Uning matnini to'liq, asl tilida, tuzilishini saqlab yozib ber. "
                     "Jadvallarni qatorlar bilan yoz. Hech narsa qo'shma va izoh berma.", uploaded],
                    role="fast", purpose=PURPOSE, temperature=0)
                d.text = d.text[: self.acfg.max_doc_tokens * 4]
                d.mode, d.note = "text", (d.note + "; " if d.note else "") + "skaner — AI o'qidi"
            except Exception as e:
                d.mode, d.note = "skipped", f"skaner o'qilmadi: {type(e).__name__}"
                log.warning("chat.scan_failed", extra={"file": d.filename, "err": str(e)[:200]})
            finally:
                if uploaded is not None:
                    await self.gemini.delete_file(uploaded.name)

    # ── questions ───────────────────────────────────────────────────────────
    async def _budget_ok(self) -> bool:
        local_midnight = datetime.combine(now_utc().astimezone(TASHKENT).date(), time(0), TASHKENT)
        async with self.sessionmaker() as s:
            n = await s.scalar(select(func.count()).select_from(LLMCall).where(
                LLMCall.purpose == PURPOSE, LLMCall.created_at >= as_utc(local_midnight))) or 0
        return n < self.cfg.max_requests_per_day

    async def ask(self, key: str, question: str) -> dict:
        """Add the question and Gemini's answer to the chat; returns the answer message."""
        question = (question or "").strip()[:MAX_QUESTION]
        if not question:
            raise ChatError("Savol bo'sh.")
        lock = self._ask_lock.setdefault(key, asyncio.Lock())
        async with lock:
            chat = await self.get(key)
            if not chat:
                raise ChatError("Bunday suhbat yo'q — lot havolasini qaytadan oching.")
            if chat.get("status") != "ready":
                raise ChatError("Lot hali o'qilmoqda — bir oz kuting.")
            if not await self._budget_ok():
                raise ChatError(f"Bugungi AI limiti tugadi ({self.cfg.max_requests_per_day} ta savol). "
                                "Ertaga davom eting yoki config.yaml dagi tenders.chat limitini oshiring.")
            tag = new_tag()
            system = self.prompt.render(untrusted_rule=untrusted_rule(tag))
            history = chat.get("messages", [])[-HISTORY_TURNS * 2:]
            convo = "\n\n".join(f"{'FOYDALANUVCHI' if m['role'] == 'user' else 'SIZ'}: {m['text']}" for m in history)
            busy = await self._deposit_busy()
            prompt = "\n".join([
                f"Bugun: {now_utc().astimezone(TASHKENT):%d.%m.%Y %H:%M} (Toshkent).",
                "", "## KOMPANIYA PROFILI (ishonchli ma'lumot)",
                yaml.safe_dump(self.company, allow_unicode=True, sort_keys=False).strip(),
                f"Zakalat limiti: ${self.limit_usd:,.0f} (jami), boshqa lotlarda band: ${busy:,.0f}.",
                "", "## LOT MA'LUMOTLARI VA HUJJATLAR",
                wrap_untrusted("lot", chat.get("context", ""), tag),
                "", "## SUHBAT TARIXI", convo or "(yo'q)",
                "", "## YANGI SAVOL", question,
            ])
            try:
                answer = await self.gemini.generate_text(prompt, role="strong", system=system, purpose=PURPOSE,
                                                         temperature=0.2)
            except Exception as e:
                log.warning("chat.ask_failed", extra={"lot": key, "err": str(e)[:300]})
                raise ChatError(f"AI javob bermadi ({type(e).__name__}). Birozdan keyin qayta so'rang.") from e
            q = {"role": "user", "text": question, "at": _now()}
            a = {"role": "ai", "text": answer.strip(), "at": _now()}
            chat["messages"] = [*chat.get("messages", []), q, a][-200:]
            await self._put(key, chat)
            return a

    async def _deposit_busy(self) -> float:
        from app.db.models import LotStatus

        async with self.sessionmaker() as s:
            lots = (await s.scalars(select(Lot).where(Lot.status == LotStatus.BIDDING))).all()
        return sum(cards.pledge_usd(lot) or 0 for lot in lots)

    async def clear(self, key: str) -> None:
        chat = await self.get(key)
        if chat:
            chat["messages"] = []
            await self._put(key, chat)


def deadline_text(chat: dict) -> str:
    d = chat.get("deadline")
    return fmt_local(datetime.fromisoformat(d)) if d else "noma'lum"
