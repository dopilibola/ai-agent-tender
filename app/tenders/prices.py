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
RATES_TTL_S = 600
CMP_TTL_S = 60
KV_AI = "pai:"  # Gemini summary of one goods lot (app/tenders/goods_ai.py)
MIN_HITS = 3  # fewer matching Uzum products → no comparison
MAX_RATIO = 20  # 20× apart = a different product (industrial transformer vs a retail one), not a price gap
MAX_UNIT_UZS = 200e6  # one item dearer than this is not sold on Uzum

PIECE_UNITS = ("дона", "dona", "шт", "штук", "комплект", "komplekt", "тўплам", "to'plam", "пачка", "pachka",
               "упаков", "қадоқ", "qadoq", "жуфт", "juft", "пар", "рулон", "rulon", "флакон", "flakon", "қути",
               "quti", "коробк", "набор", "блок", "blok", "единиц", "бирлик", "birlik", "экземпляр", "nusxa",
               "жамланма", "jamlanma", "компл", "рул")
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
    else:  # xarid_tanlov, xarid_auction, hayotbirja keep ready item lines in raw["items"]
        out = [Item.from_json(x) for x in raw.get("items") or [] if isinstance(x, dict)]
    return [i for i in out if i.name]


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def details_from_record(rec: LotRecord) -> dict[str, Any]:
    """The lot card fields shown on the lot page and given to the AI summary."""
    return {
        "title": rec.title, "description": (rec.description or "")[:4000], "type": rec.lot_type,
        "customer": rec.customer_name, "tin": rec.customer_tin, "region": rec.customer_region,
        "cost": rec.start_cost, "cur": rec.currency, "published": _iso(rec.published_at),
        "deadline": _iso(rec.deadline_at), "pledge_pct": rec.pledge_percent, "pledge": rec.pledge_amount,
        "advance_pct": rec.advance_percent, "pay_days": rec.payment_days, "exec_days": rec.execution_days,
        "warranty": rec.warranty, "valuation": rec.valuation_method, "financing": rec.financing_source,
        "qualification": [q for q in rec.qualification if q][:20], "languages": rec.languages,
        "status": rec.platform_status, "files": [{"label": f.label, "name": f.name or "", "ext": f.ext or ""}
                                                for f in rec.files][:30],
    }


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
    status: str = "ok"
    family: bool = False  # same name, different prices in this lot: compared against one Uzum price
    note: str = ""  # AI estimate: what the price is based on
    ratio: float = field(init=False)
    overpay: float = field(init=False)

    def __post_init__(self) -> None:
        ok = self.status in PRICED and self.uzum_med
        self.ratio = self.price_uzs / self.uzum_med if ok else 0
        self.overpay = (self.price_uzs - self.uzum_med) * (self.item.qty or 1) if ok else 0

    @property
    def id(self) -> str:
        return f"{self.lot.id}-{self.idx}"

    @property
    def uzum_url(self) -> str:
        return search_url(self.locale, self.query)

    @property
    def status_text(self) -> str:
        text = STATUS.get(self.status, self.status)
        return text + " · bir nomda har xil narxli" if self.family and self.status == "ok" else text


@dataclass
class LotPrices:
    """One lot with every item line and how far the price check got."""

    lot: CatalogRow
    rows: list[PriceRow]

    @property
    def id(self) -> str:
        return self.lot.id

    @property
    def compared(self) -> list[PriceRow]:
        """Rows with a reference price (Uzum or the AI estimate)."""
        return [r for r in self.rows if r.status in PRICED]

    @property
    def on_uzum(self) -> list[PriceRow]:
        return [r for r in self.rows if r.status == "ok"]

    def dearer(self, big: float) -> list[PriceRow]:
        return [r for r in self.compared if r.ratio >= big]

    def overpay(self, big: float) -> float:
        return sum(r.overpay for r in self.dearer(big))


def _merged(items: list[Item]) -> list[tuple[int, Item, bool]]:
    """(index, item, family?). Same name + same price in one lot → one row (quantities added). A name used for
    several items with very different prices (tyres of every size, «spare parts» …) is a family, not one
    product: such items are kept one by one and flagged, they are not searched on Uzum."""
    prices: dict[str, list[float]] = {}
    for it in items:
        prices.setdefault(norm(it.name), []).append(it.price)
    out: dict[tuple[str, float], tuple[int, Item, bool]] = {}
    for idx, it in enumerate(items):
        ps = [p for p in prices[norm(it.name)] if p > 0]
        family = len(ps) > 1 and max(ps) > 2 * min(ps)
        key = (norm(it.name), it.price) if not family else (f"#{idx}", it.price)
        if key in out:
            first = out[key][1]
            first.qty = (first.qty or 0) + (it.qty or 0) or None
        else:
            out[key] = (idx, Item(it.name, it.qty, it.unit, it.price, it.code), family)
    return list(out.values())


STATUS = {
    "ok": "Solishtirildi",
    "pending": "Navbatda — telefon hali qidirmagan",
    "nomatch": "Uzum'da mos mahsulot topilmadi",
    "unit": "O'lchov birligi solishtirilmaydi (kg, litr, metr …)",
    "family": "Bir nomda har xil narxli mahsulotlar",
    "far": "20 baravardan ortiq farq — boshqa mahsulot",
    "dear": "1 donasi 200 mln so'mdan qimmat",
    "noprice": "Narx ko'rsatilmagan",
    "ai": "AI bozor narxi taxmini (Uzum'da solishtirib bo'lmadi)",
}
PRICED = ("ok", "ai")  # rows that carry a reference price: Uzum median or the AI market estimate
AI_PRICE_FOR = ("unit", "nomatch", "far", "dear", "ai")  # no Uzum price → the AI summary estimates one


def item_status(it: Item, family: bool, rate: float) -> str | None:  # noqa: ARG001 (family: compared too now)
    """Why an item is not searched on Uzum, or None if it is."""
    if it.price <= 0:
        return "noprice"
    if not is_piece(it.unit) or not stems(it.name):
        return "unit"
    if it.price * rate > MAX_UNIT_UZS:
        return "dear"
    return None


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
        self.analyzer = None  # GoodsAnalyzer, set in main when Gemini is configured
        self._ai_tasks: dict[str, asyncio.Task] = {}
        self.next_run_at: Callable[[], datetime | None] = lambda: None
        # Page caches. Every write of pitems:/uzum:/pai: goes through _kv_set or _prune, which bump _gen,
        # so a cached result is reused only while none of its inputs changed.
        self._gen = 0
        self._rates_cache: tuple[float, dict[str, float]] | None = None
        self._cmp_cache: tuple[tuple, list[PriceRow], dict[str, Any]] | None = None
        self._infos_cache: tuple[tuple, dict[str, dict[str, Any]]] | None = None
        self._match_memo: dict[tuple, tuple] = {}

    async def run(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                lots = await self._open_goods()
                fetched = await self._fetch_items(lots)
                await self._prune({lot.id for lot in lots})
                searched, phone = await self._search(lots)
                analyzed = await self._analyze(lots)
            except Exception:
                log.exception("prices.run_failed")
                return
            await self._kv_set(KV_STATE, {"at": now_utc().isoformat(), "fetched": fetched, "searched": searched,
                                          "phone": phone, "analyzed": analyzed})
            log.info("prices.run", extra={"lots": len(lots), "fetched": fetched, "searched": searched,
                                          "phone": phone, "analyzed": analyzed})
        await self.warm()

    async def warm(self) -> None:
        """Build the page caches now, so the next visitor of «Barcha tenderlar» / «Uzum narxlari» doesn't wait."""
        try:
            await self.comparison()
            await self.lot_infos()
        except Exception:
            log.exception("prices.warm_failed")

    async def _open_goods(self) -> list[CatalogRow]:
        rows, _ = await self.tenders.catalog()
        now = now_utc()
        return [r for r in rows if r.kind == "goods" and (not r.deadline_at or as_utc(r.deadline_at) > now)]

    async def _rates(self) -> dict[str, float]:
        """UZS per 1 unit of each currency (CBU rates change once a day; re-read every 10 minutes)."""
        loop_now = asyncio.get_running_loop().time()
        if self._rates_cache and loop_now - self._rates_cache[0] < RATES_TTL_S:
            return dict(self._rates_cache[1])
        out = await self._read_rates()
        self._rates_cache = (loop_now, out)
        return dict(out)

    async def _read_rates(self) -> dict[str, float]:
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
        have = await self._items()
        rates = await self._rates()

        def size(r: CatalogRow) -> float:
            return (r.start_cost or 0) * rates.get(r.currency, 0)

        mine = [r for r in lots if r.source in self.tenders.sources]
        new = sorted((r for r in mine if r.id not in have), key=size, reverse=True)
        old = sorted((r for r in mine if r.id in have and not have[r.id].get("d")), key=size, reverse=True)
        todo, phone_left = [], self.cfg.max_phone_lots_per_run
        for r in new + old:  # cards read before details were kept: once more
            if len(todo) >= self.cfg.max_lots_per_run:
                break
            if getattr(self.tenders.sources[r.source], "phone_based", False):
                if phone_left <= 0:  # each phone card takes ~10 s of the shared phone
                    continue
                phone_left -= 1
            todo.append(r)
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
                                                 "items": [i.to_json() for i in items_from_record(rec)],
                                                 "d": details_from_record(rec)})
            done += 1
        return done

    async def _prune(self, open_ids: set[str]) -> None:
        async with self.sessionmaker() as s:
            keys = set(await s.scalars(select(KV.key).where(KV.key.like(KV_ITEMS + "%"))))
            stale = [k for k in keys if k[len(KV_ITEMS):] not in open_ids]
            ai = set(await s.scalars(select(KV.key).where(KV.key.like(KV_AI + "%"))))
            stale += [k for k in ai if k[len(KV_AI):] not in open_ids]
            if stale:
                await s.execute(delete(KV).where(KV.key.in_(stale)))
                self._gen += 1
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
            for _, it, family in _merged([Item.from_json(d) for d in v.get("items") or []]):
                if item_status(it, family, rate):
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

    async def lot_detail(self, lot: CatalogRow) -> dict[str, Any]:
        """Lot card fields; lots fetched before details were kept are opened once more and cached."""
        async with self.sessionmaker() as s:
            kv = await s.get(KV, KV_ITEMS + lot.id)
        value = dict(kv.value or {}) if kv else {}
        if value.get("d"):
            return value["d"]
        src = self.tenders.sources.get(lot.source)
        if src is None:
            return {}
        try:
            rec = await asyncio.wait_for(src.get_lot(lot.external_id), 40)
        except Exception as e:
            log.warning("prices.detail_failed", extra={"lot": lot.id, "err": str(e)[:200]})
            return {}
        value["d"] = details_from_record(rec)
        value.setdefault("items", [i.to_json() for i in items_from_record(rec)])
        value.setdefault("cur", rec.currency or lot.currency)
        value.setdefault("at", now_utc().isoformat())
        await self._kv_set(KV_ITEMS + lot.id, value)
        return value["d"]

    async def _analyze(self, lots: list[CatalogRow]) -> int:
        """AI summary (verdict, win %) for goods lots that have none yet, biggest first, within the daily cap."""
        if self.analyzer is None or not self.cfg.ai_per_run:
            return 0
        items = await self._items()
        async with self.sessionmaker() as s:
            done = {kv.key[len(KV_AI):]: kv.value or {} for kv in
                    (await s.scalars(select(KV).where(KV.key.like(KV_AI + "%")))).all()}
        rates = await self._rates()
        soon = now_utc() + timedelta(hours=3)

        def wanted(r: CatalogRow) -> bool:
            a = done.get(r.id)
            fresh_err = a and a.get("error") and a.get("_at", "") > (now_utc() - timedelta(days=1)).isoformat()
            return (bool(items.get(r.id, {}).get("d")) and not fresh_err
                    and (not a or a.get("error") or "item_prices" not in a)
                    and not (r.deadline_at and as_utc(r.deadline_at) < soon))

        todo = sorted(filter(wanted, lots), key=lambda r: (r.start_cost or 0) * rates.get(r.currency, 0),
                      reverse=True)[: self.cfg.ai_per_run]
        if not todo:
            return 0
        rows, _ = await self.comparison()
        by_lot = {lp.id: lp for lp in group_by_lot(rows)}
        n = 0
        for r in todo:
            if await self.analyzer.requests_today() >= self.analyzer.max_per_day:
                break
            lp = by_lot.get(r.id) or LotPrices(r, [])
            await self._make_ai(lp, items[r.id]["d"])
            n += 1
        return n

    async def lot_infos(self) -> dict[str, dict[str, Any]]:
        """Per goods lot: deposit (from its card) and AI verdict / win chance, for the «Barcha tenderlar» table."""
        rates = await self._rates()
        key = (self._gen, tuple(sorted(rates.items())))
        if self._infos_cache and self._infos_cache[0] == key:
            return {k: dict(v) for k, v in self._infos_cache[1].items()}
        out = await self._lot_infos(rates)
        self._infos_cache = (key, out)
        return {k: dict(v) for k, v in out.items()}

    async def _lot_infos(self, rates: dict[str, float]) -> dict[str, dict[str, Any]]:
        items = await self._items()
        async with self.sessionmaker() as s:
            ai = {kv.key[len(KV_AI):]: kv.value or {} for kv in
                  (await s.scalars(select(KV).where(KV.key.like(KV_AI + "%")))).all()}
        out: dict[str, dict[str, Any]] = {}
        for lot_id, v in items.items():
            d = v.get("d")
            if not d:
                continue
            pct, amount = d.get("pledge_pct"), d.get("pledge")
            rate = rates.get(d.get("cur") or v.get("cur") or "UZS")
            info: dict[str, Any] = {"pledge_pct": pct or None,
                                    "pledge_uzs": (amount * rate if amount and rate else None) if pct else 0.0}
            info["advance_pct"] = d.get("advance_pct")
            a = ai.get(lot_id) or {}
            if a and not a.get("error"):
                info["ai_verdict"] = a.get("supplier_verdict") or ""
                info["win"] = a.get("win_chance_percent") or None
            out[lot_id] = info
        return out

    @staticmethod
    def ai_sig(lp: LotPrices) -> str:
        """The summary is redone when more items of the lot got an Uzum price."""
        return f"{len(lp.rows)}:{len(lp.on_uzum)}"

    async def ai_summary(self, lp: LotPrices, detail: dict[str, Any], *, fresh: bool = False
                         ) -> tuple[dict | None, bool]:
        """(cached summary or None, is one being prepared now). Starts preparing in the background."""
        async with self.sessionmaker() as s:
            kv = await s.get(KV, KV_AI + lp.id)
        cached = dict(kv.value or {}) if kv else None
        task = self._ai_tasks.get(lp.id)
        busy = task is not None and not task.done()
        stale = fresh or not cached or (not cached.get("error") and (
            cached.get("_sig") != self.ai_sig(lp) or "item_prices" not in cached))
        if self.analyzer is not None and stale and not busy:
            self._ai_tasks[lp.id] = asyncio.create_task(self._make_ai(lp, detail))
            busy = True
        return cached, busy

    async def _make_ai(self, lp: LotPrices, detail: dict[str, Any]) -> None:
        try:
            data = await self.analyzer(lp, detail, big=self.cfg.big_ratio)
        except Exception as e:  # never leave the page spinning
            log.exception("prices.ai_failed", extra={"lot": lp.id})
            data = {"error": f"{type(e).__name__}: {e}"[:300]}
        data["_sig"] = self.ai_sig(lp)
        data["_at"] = now_utc().isoformat()
        await self._kv_set(KV_AI + lp.id, data)

    async def _kv_set(self, key: str, value: dict) -> None:
        self._gen += 1
        async with self.sessionmaker() as s:
            row = await s.get(KV, key)
            if row is None:
                s.add(KV(key=key, value=value))
            else:
                row.value = value
            await s.commit()

    async def comparison(self) -> tuple[list[PriceRow], dict[str, Any]]:
        """Every item line of every open goods lot that has its card cached, with a status
        (only status «ok» rows carry an Uzum price).

        Building this matches tens of thousands of Uzum titles, so the result is reused until a price input
        changes (new card, Uzum search or AI summary, a new scan, new rates) or a minute passes (deadlines)."""
        rates = await self._rates()
        key = (self._gen, getattr(self.tenders, "catalog_version", None), tuple(sorted(rates.items())),
               int(asyncio.get_running_loop().time() // CMP_TTL_S))
        if self._cmp_cache and self._cmp_cache[0] == key:
            return list(self._cmp_cache[1]), dict(self._cmp_cache[2])
        rows, state = await self._comparison(rates)
        self._cmp_cache = (key, rows, state)
        return list(rows), dict(state)

    def _match(self, key: str, c: dict, text: str, loc: str) -> tuple:
        """match_stats of one cached Uzum search, remembered while that search result stays the same."""
        memo_key = (key, c.get("at"), text, loc)
        hit = self._match_memo.get(memo_key)
        if hit is None:
            hits = [Hit(x.get("t") or "", x.get("p") or 0, x.get("u") or "") for x in c.get("hits") or []]
            hit = match_stats(text, hits, loc)
            if len(self._match_memo) > 50_000:
                self._match_memo.clear()
            self._match_memo[memo_key] = hit
        return hit

    async def _comparison(self, rates: dict[str, float]) -> tuple[list[PriceRow], dict[str, Any]]:
        lots = {r.id: r for r in await self._open_goods()}
        items = await self._items()
        cache = await self._uzum_cache()
        async with self.sessionmaker() as s:
            st = await s.get(KV, KV_STATE)
            ai = {kv.key[len(KV_AI):]: (kv.value or {}) for kv in
                  (await s.scalars(select(KV).where(KV.key.like(KV_AI + "%")))).all()}
        state = dict(st.value or {}) if st else {}
        rows: list[PriceRow] = []
        for lot_id, v in items.items():
            lot = lots.get(lot_id)
            rate = rates.get(v.get("cur") or "UZS")
            if lot is None or not rate:
                continue
            a = ai.get(lot_id) or {}
            est = a.get("item_prices") or {}
            ai_at = datetime.fromisoformat(a["_at"]) if a.get("_at") else None
            for idx, it, family in _merged([Item.from_json(d) for d in v.get("items") or []]):
                loc, text = search_query(it.name)
                base = (lot, idx, it, it.price * rate)
                status = item_status(it, family, rate)
                qk = query_key(loc, text)
                c = None if status else cache.get(qk)
                if status is None and not c:
                    status = "pending"
                if status:
                    row = PriceRow(*base, 0, 0, 0, text, loc, None, None, status, family)
                else:
                    med, mn, n, best = self._match(qk, c, text, loc)
                    at = datetime.fromisoformat(c["at"]) if c.get("at") else None
                    if med is None:
                        row = PriceRow(*base, 0, 0, n, text, loc, best, at, "nomatch", family)
                    elif not 1 / MAX_RATIO <= it.price * rate / med <= MAX_RATIO:
                        row = PriceRow(*base, med, mn or med, n, text, loc, best, at, "far", family)
                    else:
                        row = PriceRow(*base, med, mn or med, n, text, loc, best, at, "ok", family)
                e = est.get(str(idx))
                if row.status in AI_PRICE_FOR and e and e.get("p", 0) > 0:
                    row = PriceRow(*base, e["p"], e["p"], 0, text, loc, None, ai_at, "ai", family, e.get("n") or "")
                rows.append(row)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
        searchable = sum(v for k, v in counts.items() if k in ("ok", "pending", "nomatch", "far"))
        state.update({"lots": len(lots), "lots_with_items": sum(1 for k in items if k in lots), "items": len(rows),
                      "piece": searchable, "pending": counts.get("pending", 0), "compared": counts.get("ok", 0),
                      "ai_priced": counts.get("ai", 0),
                      "status_counts": counts})
        return rows, state


def group_by_lot(rows: list[PriceRow]) -> list[LotPrices]:
    out: dict[str, LotPrices] = {}
    for r in rows:
        out.setdefault(r.lot.id, LotPrices(r.lot, [])).rows.append(r)
    return list(out.values())
