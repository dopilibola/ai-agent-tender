"""xarid.uzex.uz — «Auksion» (e-auction), read from the public pages in the phone's own Chrome.

The auction API answers only requests that carry a token made by the site's own JavaScript, so the bot
does not call it: the phone opens https://xarid.uzex.uz/auction/list like a person, reads the lot cards on
screen (20 a page) and presses the next page number. Lot cards (/auction/detail/<id>) are opened only for
IT-looking categories (and by the goods page), and are cached on disk.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.tenders.schemas import LotFile, LotRecord, LotSummary
from app.tenders.sources.hayotbirja import item_line
from app.tenders.sources.phone_browser import CardCache, PhoneBrowser
from app.tenders.timeutil import TASHKENT, now_utc

log = logging.getLogger(__name__)

SITE = "https://xarid.uzex.uz"
LIST_URL = f"{SITE}/auction/list"
LOT_TYPE = "Auksion"
OPEN_STATUS = "Опубликован"
MAX_PAGES = 60  # 20 lots a page; ~550 open lots on 27.11.2026

LIST_READY_JS = "document.querySelectorAll('.lot-item a[href*=\"/auction/detail/\"]').length"
LIST_JS = r"""(() => {
  const T = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('.lot-item')].map(c => {
    const a = c.querySelector('a[href*="/auction/detail/"]');
    const q = s => T(c.querySelector(s) && c.querySelector(s).innerText);
    return {id: a ? (a.getAttribute('href').match(/detail\/(\d+)/) || [])[1] : null, num: q('.lot__number'),
            start: q('.lot__price__start'), cat: q('.lot__category'), addr: q('.lot__address'), time: q('.lot__time')};
  }).filter(r => r.id);
  const active = document.querySelector('li.pagination-page.active');
  const total = (document.body.innerText.match(/Katalogda\s+(\d+)\s+Lot/) || [])[1];
  return {rows, page: active ? Number(T(active.innerText)) : 1, total: total ? Number(total) : null};
})()"""
# Press the page number after the active one; false when this is the last page.
NEXT_JS = r"""(() => {
  const active = document.querySelector('li.pagination-page.active');
  const next = active && active.nextElementSibling;
  const a = next && next.classList.contains('pagination-page') && next.querySelector('a');
  if (!a) return false;
  a.click();
  return true;
})()"""
FIRST_ID_JS = ("(() => { const a = document.querySelector('.lot-item a[href*=\"/auction/detail/\"]');"
               " return a ? a.getAttribute('href') : ''; })()")

DETAIL_READY_JS = "document.querySelectorAll('.lot__info__info .row').length"
DETAIL_JS = r"""(() => {
  const T = s => (s || '').replace(/\s+/g, ' ').trim();
  const info = {};
  document.querySelectorAll('.lot__info__info .row').forEach(r => {
    const c = r.children;
    if (c.length >= 2) info[T(c[0].innerText).replace(/:$/, '')] = T(c[1].innerText);
  });
  const tables = [...document.querySelectorAll('table')];
  const heads = t => [...t.querySelectorAll('th')].map(th => T(th.innerText));
  const bid = tables.find(t => heads(t).includes('Tovar'));
  const products = bid ? [...bid.querySelectorAll('tbody tr')].map(tr => {
    const td = tr.querySelectorAll('td');
    return {n: T(td[1] && td[1].innerText), q: T(td[2] && td[2].innerText)};
  }) : [];
  const specs = tables.filter(t => heads(t).some(h => /O‘lchov birligi/.test(h)))
    .map(t => [...t.querySelectorAll('tbody td')].map(td => T(td.innerText)));
  const body = document.body.innerText;
  const cat = body.match(/Toifa:\s*([^\n]+)/);
  return {num: (body.match(/Lot raqami:\s*(\d+)/) || [])[1] || '', cat: cat ? T(cat[1]) : '', info, products,
          specs, descs: [...body.matchAll(/batafsil tavsifi:\s*([^\n]+)/g)].map(x => T(x[1]))};
})()"""


def lot_url(lot_id: int | str) -> str:
    return f"{SITE}/auction/detail/{lot_id}"


def _clean(v: Any) -> str:
    return " ".join(str(v).replace("\xa0", " ").split()) if v else ""


def parse_money(text: str) -> float | None:
    """'Boshlang‘ich narx: 3 000 000,00 So'm' → 3000000.0"""
    m = re.search(r"(\d[\d\s\xa0]*(?:[.,]\d+)?)", (text or "").split(":", 1)[-1])
    if not m:
        return None
    return float(re.sub(r"[\s\xa0]", "", m.group(1)).replace(",", "."))


def parse_dt(text: str) -> datetime | None:
    """'… 30.11.2026, 10:00:00' (Tashkent) → aware UTC"""
    m = re.search(r"(\d\d)\.(\d\d)\.(\d{4}),?\s+(\d\d):(\d\d)(?::(\d\d))?", text or "")
    if not m:
        return None
    d, mo, y, h, mi, s = m.groups()
    return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=TASHKENT).astimezone(UTC)


def _after(text: str, label: str) -> str:
    return _clean(text.split(label, 1)[-1]) if label in (text or "") else _clean(text)


def parse_list_row(r: dict) -> LotSummary:
    return LotSummary(
        source="xarid_auction",
        external_id=str(r["id"]),
        url=lot_url(r["id"]),
        title=_after(r.get("cat", ""), "Toifa:") or f"Lot {r['id']}",
        deadline_at=parse_dt(r.get("time", "")),
        start_cost=parse_money(r.get("start", "")),
        currency="UZS",
        customer_region=_clean(r.get("addr")) or None,
        lot_type=LOT_TYPE,
        title_is_category=True,
    )


def _num(v: str) -> float | None:
    v = re.sub(r"[^\d.,]", "", v or "").replace(",", ".")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def parse_lot(lot_id: str, d: dict) -> LotRecord:
    info = d.get("info") or {}
    specs = list({tuple(s) for s in d.get("specs") or [] if s})  # the page repeats each table once
    specs.sort(key=lambda s: d["specs"].index(list(s)))
    items = []
    for i, p in enumerate(d.get("products") or []):
        spec = specs[i] if i < len(specs) else ()
        unit = spec[1] if len(spec) > 1 else ""
        price = parse_money(spec[3]) if len(spec) > 3 else None
        items.append({"n": _clean(p.get("n")), "q": _num(p.get("q")), "u": unit, "p": price or 0})
    items = [i for i in items if i["n"]]
    descs = [x for x in dict.fromkeys(d.get("descs") or []) if x]
    lines = [item_line(i) for i in items]
    total = sum((i["p"] or 0) * (i["q"] or 1) for i in items) or None
    end = parse_dt(info.get("Tugash sanasi", ""))
    status = info.get("Status") or None
    description = "\n".join(p for p in [
        f"Fayl tavsifi: {info['Faylni tavsiflash']}" if info.get("Faylni tavsiflash") else "",
        f"Yetkazish joyi: {info['Yetkazib berish joyi']}" if info.get("Yetkazib berish joyi") else "",
        f"Yetkazish muddati: {info['Yetkazib berish muddati']}" if info.get("Yetkazib berish muddati") else "",
        "Pozitsiyalar:", *lines, *descs,
    ] if p).strip()
    days = re.search(r"(\d+)", info.get("Yetkazib berish muddati", ""))
    return LotRecord(
        source="xarid_auction",
        external_id=str(lot_id),
        url=lot_url(lot_id),
        title="; ".join(dict.fromkeys(i["n"] for i in items))[:300] or d.get("cat") or f"Lot {lot_id}",
        description=description,
        deadline_at=end,
        published_at=parse_dt(info.get("Boshlanish sanasi", "")),
        start_cost=total,
        currency="UZS",
        customer_name=info.get("Buyurtmachining nomi") or None,
        customer_region=info.get("Manzil") or None,
        lot_type=LOT_TYPE,
        execution_days=int(days.group(1)) if days else None,
        valuation_method="Auksion (eng past narx)",
        price_weight=100.0,
        tech_weight=0.0,
        platform_status=status,
        is_open=status == OPEN_STATUS and (end is None or end > now_utc()),
        raw={"items": items, "num": d.get("num"), "category": d.get("cat")},
    )


class XaridAuctionSource:
    name = "xarid_auction"
    title = "xarid.uzex.uz (auksion)"
    scan_descriptions = False  # cards only for IT categories
    phone_based = True

    def __init__(self, browser: PhoneBrowser, cache_dir: Path):
        self.browser = browser
        self.cards = CardCache(cache_dir / "xarid_auction")

    async def list_open(self) -> list[LotSummary]:
        out: dict[str, LotSummary] = {}
        async with self.browser.page(LIST_URL) as tab:
            await self.browser.wait_for(tab, LIST_READY_JS)
            for _ in range(MAX_PAGES):
                data = await tab.eval(LIST_JS) or {}
                for r in data.get("rows") or []:
                    lot = parse_list_row(r)
                    out.setdefault(lot.external_id, lot)
                first = await tab.eval(FIRST_ID_JS)
                if not await tab.eval(NEXT_JS):
                    break
                # wait for the next page to replace the cards
                await self.browser.wait_for(
                    tab, f"(() => {{ const v = {FIRST_ID_JS}; return v && v !== {first!r}; }})()", limit_s=20)
        if not out:
            raise ValueError("xarid auksion: ro'yxat bo'sh (sahifa ochilmadi?)")
        log.info("xarid_auction.list_open", extra={"count": len(out)})
        return list(out.values())

    async def get_lot(self, external_id: str) -> LotRecord:
        cached = self.cards.get(external_id)
        if cached is None:
            cached = await self.browser.read(lot_url(external_id), DETAIL_READY_JS, DETAIL_JS)
            if not cached or not cached.get("info"):
                raise ValueError(f"xarid auksion {external_id}: karta o'qilmadi")
            self.cards.put(external_id, cached)
        return parse_lot(external_id, cached)

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        raise RuntimeError("auksion fayllari faqat lot sahifasida")

    async def aclose(self) -> None:
        return None
