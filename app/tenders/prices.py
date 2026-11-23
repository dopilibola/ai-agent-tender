"""Uzum Market price check for goods lots («Barcha tenderlar» → «Uzum narxlari»).

1. Item lines (name, quantity, unit, unit price) come from the lot cards of open goods lots
   (etender `budget_products`, xarid `details`), fetched once per lot and kept in KV `pitems:<lot id>`.
2. The phone's own Chrome opens uzum.uz search for an item name — the same page a person sees — and
   the product cards on screen are read (title + current price). Results are cached per query in
   KV `uzum:<hash>` for a few days, so the phone only searches each name once in a while.
3. The page compares the tender unit price with the median price of the Uzum results that really
   match the name (most words of the name must be in the Uzum title).

Only piece-like units (dona, komplekt, pachka …) are compared: kilograms, litres and metres are not
sold per unit on Uzum, so their prices would not be comparable.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete, select

from app.db.models import KV
from app.tenders.catalog import CatalogRow
from app.tenders.schemas import LotRecord
from app.tenders.timeutil import as_utc, now_utc

log = logging.getLogger(__name__)

KV_ITEMS = "pitems:"
KV_UZUM = "uzum:"
KV_STATE = "uzum_state"
MIN_HITS = 3  # fewer matching Uzum products → no comparison
MAX_RATIO = 20  # 20× apart = a different product (industrial transformer vs a retail one), not a price gap
MAX_UNIT_UZS = 200e6  # one item dearer than this is not sold on Uzum

PIECE_UNITS = ("дона", "dona", "шт", "штук", "комплект", "komplekt", "тўплам", "to'plam", "пачка", "pachka",
               "упаков", "қадоқ", "qadoq", "жуфт", "juft", "пар", "рулон", "rulon", "флакон", "flakon", "қути",
               "quti", "коробк", "набор", "блок", "blok", "единиц", "бирлик", "birlik", "экземпляр", "nusxa")
STOP = {"uchun", "bilan", "va", "yoki", "для", "и", "с", "на", "по", "из", "в", "или", "учун", "билан", "ва",
        "сотиб", "olish", "олиш", "харид", "xarid", "qilish", "қилиш", "yetkazib", "етказиб", "berish", "бериш",
        "поставка", "закупка", "приобретение", "товар", "tovar", "mahsulot", "маҳсулот", "маҳсулотлари",
        "mahsulotlari", "turli", "турли", "xil", "хил"}

UZ_CYR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
          "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
          "у": "u", "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "",
          "э": "e", "ю": "yu", "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h"}
UZ_ONLY = set("ўқғҳ")
UZ_CYR_WORDS = re.compile(r"(?:^|\s)(?:учун|билан|ва)(?:\s|$)|[а-я](?:ган|лар|лари|ларни|ини)(?:\s|$)")
# Directions whose goods Uzum actually sells are searched first; medicines, fuel, vehicle parts … last.
RETAIL = {"tech", "furniture", "paper", "electric", "clothes", "chemical", "culture", "equipment", "food",
          "build", "other"}


# ── Items ───────────────────────────────────────────────────────────────────
@dataclass
class Item:
    name: str
    qty: float | None
    unit: str
    price: float  # unit price in the lot currency
    code: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"n": self.name, "q": self.qty, "u": self.unit, "p": self.price, "c": self.code}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Item:
        return cls(name=d.get("n") or "", qty=d.get("q"), unit=d.get("u") or "", price=d.get("p") or 0,
                   code=d.get("c") or "")


def _clean(v: Any) -> str:
    return " ".join(str(v).replace("\xa0", " ").split()) if v else ""


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def items_from_record(rec: LotRecord) -> list[Item]:
    raw = rec.raw or {}
    out: list[Item] = []
    if rec.source == "etender":
        products = raw.get("budget_products") or []
        if isinstance(products, str):
            with contextlib.suppress(ValueError):
                products = json.loads(products)
        for p in products if isinstance(products, list) else []:
            unit = next((_clean(x.get("User_Value")) for x in p.get("Js_Properties") or []
                         if isinstance(x, dict) and "лчов" in (x.get("Property_Name") or "")), "")
            out.append(Item(_clean(p.get("Product_Name") or p.get("Description")), _num(p.get("Quantity")), unit,
                            _num(p.get("Price")) or 0, _clean(p.get("Product_Code"))))
    elif rec.source == "xarid":
        for x in raw.get("details") or []:
            unit = next((_clean(p.get("propertyValueName")) for p in x.get("properties") or []
                         if isinstance(p, dict) and "измерения" in (p.get("propertyName") or "")), "")
            out.append(Item(_clean(x.get("productName") or x.get("description")), _num(x.get("quantity")), unit,
                            _num(x.get("price")) or 0, _clean(x.get("productCode"))))
    return [i for i in out if i.name]


def is_piece(unit: str) -> bool:
    u = unit.lower()
    return not u or any(u.startswith(p) for p in PIECE_UNITS)


# ── Query + matching ───────────────────────────────────────────────────────
def norm(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[ʻʼ’‘`']", "", t)
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def translit(text: str) -> str:
    out = []
    for ch in text:
        t = UZ_CYR.get(ch.lower())
        out.append(ch if t is None else t.capitalize() if ch.isupper() else t)
    return "".join(out)


def search_query(name: str) -> tuple[str, str]:
    """(locale, text) for uzum.uz search. Uzbek Cyrillic → Latin (Uzum is Latin in uz), Russian → ru."""
    text = re.sub(r"\([^)]*\)", " ", name)  # «(на закупку …)» and codes in brackets add noise
    text = re.split(r"[;/\n]", text)[0]
    words = [w for w in re.sub(r"[«»\"“”]", " ", text).split() if w]
    text = " ".join(words[:7])[:80]
    low = text.lower()
    if any(ch in UZ_ONLY for ch in low) or UZ_CYR_WORDS.search(low):
        return "uz", translit(text)
    if re.search(r"[а-я]", low):
        return "ru", text
    return "uz", text


def search_url(locale: str, text: str) -> str:
    return f"https://uzum.uz/{locale}/search?query={quote(text)}"


def query_key(locale: str, text: str) -> str:
    return KV_UZUM + hashlib.sha1(f"{locale}|{norm(text)}".encode()).hexdigest()[:24]


def stems(text: str) -> list[str]:
    out = []
    for w in norm(text).split():
        if len(w) < 3 or w in STOP or w.isdigit():
            continue
        s = w[: max(5, len(w) - 4)]  # drop a suffix, keep long words specific (immunoglobulin ≠ immunitet)
        if s not in out:
            out.append(s)
    return out[:6]


def relevant(query: str, title: str, locale: str = "uz") -> bool:
    """The main noun must be in the Uzum title (last word in Uzbek, first in Russian) + ~half of the rest."""
    st = stems(query)
    if not st:
        return False
    words = norm(title).split()
    has = [any(w.startswith(s) for w in words) for s in st]
    head = has[0] if locale == "ru" else has[-1]
    return head and sum(has) >= max(1, math.ceil(len(st) * 0.4))


@dataclass
class Hit:
    title: str
    price: float
    url: str


def match_stats(query: str, hits: list[Hit], locale: str = "uz"
                ) -> tuple[float | None, float | None, int, Hit | None]:
    """(median, min, n matching, closest hit) over the Uzum products that match the query."""
    good = [x for x in hits if x.price > 0 and relevant(query, x.title, locale)]
    if len(good) < MIN_HITS:
        return None, None, len(good), good[0] if good else None
    prices = [x.price for x in good[:12]]
    med = statistics.median(prices)
    best = min(good[:12], key=lambda x: abs(x.price - med))
    return med, min(prices), len(good), best


def parse_price(text: str) -> float:
    digits = re.sub(r"[^\d]", "", text or "")
    return float(digits) if digits else 0.0


# ── Phone (Uzum search in the phone's Chrome) ─────────────────────────────
CARDS_JS = r"""(() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/product/"]')) {
    const t = a.querySelector('[data-test-id="product-card__title"]');
    const p = a.querySelector('[data-test-id="product-card__actual-price"]');
    const u = a.href.split('?')[0];
    if (!t || !p || seen.has(u)) continue;
    seen.add(u);
    out.push({t: t.innerText.trim(), p: p.innerText, u});
    if (out.length >= 24) break;
  }
  const body = (document.body && document.body.innerText || '').toLowerCase();
  const none = /hech narsa topilmadi|ничего не нашлось|ничего не найдено|topilmadi/.test(body) && !out.length;
  return {out, none};
})()"""


class UzumPhone:
    def __init__(self, phone, *, wait_s: float = 15):
        self.phone = phone
        self.wait_s = wait_s

    async def search(self, locale: str, text: str) -> list[Hit]:
        from app.phone.device import CDPError

        async with self.phone.open(search_url(locale, text)) as tab:
            loop = asyncio.get_running_loop()
            end = loop.time() + self.wait_s
            res: dict = {"out": [], "none": False}
            while loop.time() < end:
                try:
                    res = await tab.eval(CARDS_JS) or res
                except CDPError:  # the SPA navigates once while loading
                    await asyncio.sleep(1)
                    continue
                if res.get("out") or res.get("none"):
                    break
                await asyncio.sleep(1)
            if res.get("out"):
                await asyncio.sleep(1.5)  # let the rest of the first screen render, then read again
                with contextlib.suppress(CDPError):
                    res = await tab.eval(CARDS_JS) or res
        return [Hit(_clean(x.get("t")), parse_price(x.get("p")), x.get("u") or "") for x in res.get("out") or []]


# ── Comparison rows for the page ───────────────────────────────────────────
@dataclass
class PriceRow:
    lot: CatalogRow
    idx: int
    item: Item
    price_uzs: float
    uzum_med: float
    uzum_min: float
    n: int
    query: str
    locale: str
    best: Hit | None
    checked_at: datetime | None
    ratio: float = field(init=False)
    overpay: float = field(init=False)

    def __post_init__(self) -> None:
        self.ratio = self.price_uzs / self.uzum_med if self.uzum_med else 0
        self.overpay = (self.price_uzs - self.uzum_med) * (self.item.qty or 1)

    @property
    def id(self) -> str:
        return f"{self.lot.id}-{self.idx}"

    @property
    def uzum_url(self) -> str:
        return search_url(self.locale, self.query)


def _merged(items: list[Item]) -> list[tuple[int, Item]]:
    """Same name + same price in one lot → one row (quantities added). A name used for several items with
    very different prices (tyres of every size, «spare parts» …) is a family, not one product: dropped."""
    prices: dict[str, list[float]] = {}
    for it in items:
        prices.setdefault(norm(it.name), []).append(it.price)
    out: dict[tuple[str, float], tuple[int, Item]] = {}
    for idx, it in enumerate(items):
        ps = [p for p in prices[norm(it.name)] if p > 0]
        if len(ps) > 1 and max(ps) > 2 * min(ps):
            continue
        key = (norm(it.name), it.price)
        if key in out:
            first = out[key][1]
            first.qty = (first.qty or 0) + (it.qty or 0) or None
        else:
            out[key] = (idx, Item(it.name, it.qty, it.unit, it.price, it.code))
    return list(out.values())


# ── Service ─────────────────────────────────────────────────────────────────
class PriceWatch:
    """Every run: fetch item lines of new goods lots, then search a batch of item names on Uzum via the phone."""

    def __init__(self, settings, sessionmaker, tenders, *, uzum: UzumPhone | None = None):
        self.cfg = settings.config.tenders.prices
        self.sessionmaker = sessionmaker
        self.tenders = tenders
        if uzum is None:
            from app.phone.device import Phone
            p = settings.config.phone
            uzum = UzumPhone(Phone(p.adb_path, p.cdp_port, p.mdns_cmd))
        self.uzum = uzum
        self._lock = asyncio.Lock()
        self.next_run_at: Callable[[], datetime | None] = lambda: None

    async def run(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                lots = await self._open_goods()
                fetched = await self._fetch_items(lots)
                await self._prune({lot.id for lot in lots})
                searched, phone = await self._search(lots)
            except Exception:
                log.exception("prices.run_failed")
                return
            await self._kv_set(KV_STATE, {"at": now_utc().isoformat(), "fetched": fetched, "searched": searched,
                                          "phone": phone})
            log.info("prices.run", extra={"lots": len(lots), "fetched": fetched, "searched": searched,
                                          "phone": phone})

    async def _open_goods(self) -> list[CatalogRow]:
        rows, _ = await self.tenders.catalog()
        now = now_utc()
        return [r for r in rows if r.kind == "goods" and (not r.deadline_at or as_utc(r.deadline_at) > now)]

    async def _rates(self) -> dict[str, float]:
        """UZS per 1 unit of each currency."""
        fx = self.tenders.fx
        out = {"UZS": 1.0}
        if not fx:
            return out
        usd_uzs = await fx.get()
        if not usd_uzs:
            return out
        for cur in ("USD", "EUR", "RUB", "CNY", "GBP"):
            per = await fx.usd_per_unit(cur)
            if per:
                out[cur] = per * usd_uzs
        return out

    async def _fetch_items(self, lots: list[CatalogRow]) -> int:
        async with self.sessionmaker() as s:
            have = set(await s.scalars(select(KV.key).where(KV.key.like(KV_ITEMS + "%"))))
        rates = await self._rates()

        def size(r: CatalogRow) -> float:
            return (r.start_cost or 0) * rates.get(r.currency, 0)

        todo = sorted((r for r in lots if KV_ITEMS + r.id not in have and r.source in self.tenders.sources),
                      key=size, reverse=True)[: self.cfg.max_lots_per_run]
        done = fails = 0
        for r in todo:
            try:
                rec = await self.tenders.sources[r.source].get_lot(r.external_id)
            except Exception as e:
                fails += 1
                log.warning("prices.lot_failed", extra={"lot": r.id, "err": str(e)[:200]})
                if fails >= 3:
                    break
                continue
            fails = 0
            await self._kv_set(KV_ITEMS + r.id, {"at": now_utc().isoformat(), "cur": rec.currency or r.currency,
                                                 "items": [i.to_json() for i in items_from_record(rec)]})
            done += 1
        return done

    async def _prune(self, open_ids: set[str]) -> None:
        async with self.sessionmaker() as s:
            keys = set(await s.scalars(select(KV.key).where(KV.key.like(KV_ITEMS + "%"))))
            stale = [k for k in keys if k[len(KV_ITEMS):] not in open_ids]
            if stale:
                await s.execute(delete(KV).where(KV.key.in_(stale)))
            old = (now_utc() - timedelta(days=self.cfg.cache_days * 4)).isoformat()
            uz = (await s.scalars(select(KV).where(KV.key.like(KV_UZUM + "%")))).all()
            for kv in uz:
                if (kv.value or {}).get("at", "") < old:
                    await s.delete(kv)
            await s.commit()

    async def _items(self) -> dict[str, dict]:
        async with self.sessionmaker() as s:
            kvs = (await s.scalars(select(KV).where(KV.key.like(KV_ITEMS + "%")))).all()
        return {kv.key[len(KV_ITEMS):]: kv.value or {} for kv in kvs}

    async def _uzum_cache(self) -> dict[str, dict]:
        async with self.sessionmaker() as s:
            kvs = (await s.scalars(select(KV).where(KV.key.like(KV_UZUM + "%")))).all()
        return {kv.key: kv.value or {} for kv in kvs}

    async def _search(self, lots: list[CatalogRow]) -> tuple[int, str]:
        if self.cfg.max_searches_per_run <= 0:
            return 0, "off"
        by_id = {r.id: r for r in lots}
        items = await self._items()
        cache = await self._uzum_cache()
        rates = await self._rates()
        fresh_after = (now_utc() - timedelta(days=self.cfg.cache_days)).isoformat()
        want: dict[str, tuple[tuple[bool, float], str, str]] = {}  # key → ((retail?, money), locale, text)
        for lot_id, v in items.items():
            if lot_id not in by_id:
                continue
            rate = rates.get(v.get("cur") or "UZS")
            if not rate:
                continue
            for _, it in _merged([Item.from_json(d) for d in v.get("items") or []]):
                if it.price <= 0 or it.price * rate > MAX_UNIT_UZS or not is_piece(it.unit) or not stems(it.name):
                    continue
                loc, text = search_query(it.name)
                key = query_key(loc, text)
                if cache.get(key, {}).get("at", "") >= fresh_after:
                    continue
                money = it.price * rate * (it.qty or 1)
                prio = (by_id[lot_id].direction in RETAIL, money)
                if key not in want or want[key][0] < prio:
                    want[key] = (prio, loc, text)
        if not want:
            return 0, "ok"
        from app.phone.device import PHONE_LOCK, PhoneUnavailable

        todo = sorted(want.items(), key=lambda kv: kv[1][0], reverse=True)[: self.cfg.max_searches_per_run]
        done = 0
        try:
            async with PHONE_LOCK:
                await self.uzum.phone.ensure()
            for key, (_, loc, text) in todo:
                async with PHONE_LOCK:
                    hits = await self.uzum.search(loc, text)
                await self._kv_set(key, {"at": now_utc().isoformat(), "q": text, "loc": loc,
                                         "hits": [{"t": x.title[:200], "p": x.price, "u": x.url} for x in hits]})
                done += 1
                await asyncio.sleep(self.cfg.delay_s)
        except PhoneUnavailable as e:
            log.warning("prices.phone_unavailable", extra={"err": str(e)[:200]})
            return done, f"telefon: {e}"[:200]
        return done, "ok"

    async def _kv_set(self, key: str, value: dict) -> None:
        async with self.sessionmaker() as s:
            row = await s.get(KV, key)
            if row is None:
                s.add(KV(key=key, value=value))
            else:
                row.value = value
            await s.commit()

    async def comparison(self) -> tuple[list[PriceRow], dict[str, Any]]:
        lots = {r.id: r for r in await self._open_goods()}
        items = await self._items()
        cache = await self._uzum_cache()
        rates = await self._rates()
        async with self.sessionmaker() as s:
            st = await s.get(KV, KV_STATE)
        state = dict(st.value or {}) if st else {}
        rows: list[PriceRow] = []
        total = piece = pending = 0
        for lot_id, v in items.items():
            lot = lots.get(lot_id)
            rate = rates.get(v.get("cur") or "UZS")
            if lot is None or not rate:
                continue
            for idx, it in _merged([Item.from_json(d) for d in v.get("items") or []]):
                total += 1
                if it.price <= 0 or it.price * rate > MAX_UNIT_UZS or not is_piece(it.unit) or not stems(it.name):
                    continue
                piece += 1
                loc, text = search_query(it.name)
                c = cache.get(query_key(loc, text))
                if not c:
                    pending += 1
                    continue
                hits = [Hit(x.get("t") or "", x.get("p") or 0, x.get("u") or "") for x in c.get("hits") or []]
                med, mn, n, best = match_stats(text, hits, loc)
                if med is None or not 1 / MAX_RATIO <= it.price * rate / med <= MAX_RATIO:
                    continue
                at = datetime.fromisoformat(c["at"]) if c.get("at") else None
                rows.append(PriceRow(lot, idx, it, it.price * rate, med, mn or med, n, text, loc, best, at))
        state.update({"lots": len(lots), "lots_with_items": sum(1 for k in items if k in lots), "items": total,
                      "piece": piece, "pending": pending, "compared": len(rows)})
        return rows, state
