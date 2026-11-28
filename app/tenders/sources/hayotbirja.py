"""xt-xarid.uz / hayotbirja.uz (Hayot birja) — read from the public /procedure/ pages in the phone's Chrome.

robots.txt bars /api/ and /rpc/, so the bot never calls them; it opens the same list pages a person does
(/procedure/tender, /selection, /reduction, /local_reduction, /request_proposals), presses «Кўпроқ кўрсатиш»
a few times and reads the cards on screen. The shops (/ad, /nad) are product catalogues, not tenders.

List titles are often generic ("ПГЗ из Агенства №419022", "Takliflar so‘rovi"), so every new lot's card
(/procedure/<id>/core) is opened once — the «Тижорат талаблари» tab holds the positions — and the item
names replace the generic title from then on. Only the list shows the time left, so the deadline is
"now + time left", kept from the first sighting (data/phone_cards/hayotbirja_state.json) so that it does
not jitter between scans.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.tenders.schemas import LotFile, LotRecord, LotSummary
from app.tenders.sources.phone_browser import CardCache, JsonStore, PhoneBrowser
from app.tenders.timeutil import now_utc

log = logging.getLogger(__name__)

SITE = "https://xt-xarid.uz"
SECTIONS = {  # url part → lot type shown on the site
    "tender": "Tender",
    "selection": "Eng yaxshi taklifni tanlash",
    "reduction": "Auksion",
    "local_reduction": "Mahalliy auksion",
    "request_proposals": "Takliflar so'rovi",
}
OPEN_STATUSES = {"тендер очиқ", "очиқ", "эълон қилинган", "тендер открыт", "открыт", "открыта", "опубликован",
                 "опубликовано", "ochiq", "tender ochiq", "e'lon qilingan", "eʼlon qilingan"}
GENERIC = re.compile(r"^(пгз из агенства|takliflar so|таклифлар сўрови|многолотовая процедура|тендер$|tender$|"
                     r"отбор$|танлаш$|аукцион$|энг яхши таклиф|eng yaxshi taklif|отбор наилучших|lot \d+$)", re.I)
MAX_MORE = 6  # «show more» presses per section (50 cards each)
# xt-xarid.uz answers «429» (too many requests) when read too often (seen 27–28.11.2026), so be gentle:
LIST_EVERY_S = 3600       # the list pages are read at most once an hour; scans in between reuse that list
PAGE_PAUSE_S = 8          # pause after every page of this site
BACKOFF_S = 3 * 3600      # after a 429 the site is left alone this long
RATE_LIMIT_JS = r"/(^|\D)429(\D|$)|Too Many Requests|слишком много запросов/i.test(document.body.innerText.slice(0, 400))"
MAY_BE_EMPTY = {"local_reduction"}  # often has no lots at all
PAGE_WAIT_S = 45  # xt-xarid.uz pages can take half a minute to render
DEADLINE_SLACK_S = 15 * 60

LIST_READY_JS = "document.querySelectorAll('div.ui-data-view-item').length"
LIST_JS = r"""(() => [...document.querySelectorAll('div.ui-data-view-item')].map(c => {
  const a = c.querySelector('a[href*="/procedure/"][href$="/core"]');
  return {id: a ? (a.getAttribute('href').match(/procedure\/(\d+)/) || [])[1] : null,
          lines: c.innerText.split('\n').map(x => x.trim()).filter(Boolean)};
}).filter(r => r.id))()"""
# Press «show more»; false when there is no such button. Returns the card count before the press.
MORE_JS = r"""(() => {
  const b = [...document.querySelectorAll('button')].find(x => /Кўпроқ|Ko.proq|Показать ещ|Show more/i.test(
    (x.getAttribute('title') || '') + ' ' + x.innerText));
  if (!b || b.disabled) return false;
  const n = document.querySelectorAll('div.ui-data-view-item').length;
  b.click();
  return n;
})()"""
COUNT_JS = "document.querySelectorAll('div.ui-data-view-item').length"
DETAIL_READY_JS = r"/ҲОЛАТИ|СОСТОЯНИЕ|HOLATI|STATUS/i.test(document.body.innerText) && document.body.innerText.length > 1500"
DETAIL_JS = r"""(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const main = document.body.innerText;
  let more = '';
  if (!/Позиция #1|Позиция №1|Pozitsiya #1/.test(main)) {
    const tab = [...document.querySelectorAll('div.item span, div.item')].find(
      e => /Тижорат|Коммерч|Tijorat/i.test(e.textContent) && e.textContent.length < 40);
    if (tab) { tab.click(); await sleep(2500); more = document.body.innerText; }
  }
  return {main, more};
})()"""


class RateLimited(RuntimeError):
    """The site answered 429: too many requests from us."""


def lot_url(lot_id: int | str) -> str:
    return f"{SITE}/procedure/{lot_id}/core"


def _clean(v: Any) -> str:
    return " ".join(str(v).replace("\xa0", " ").split()) if v else ""


def parse_money(text: str) -> float | None:
    m = re.search(r"(\d[\d\s\xa0]*(?:\.\d+)?)", text or "")
    return float(re.sub(r"[\s\xa0]", "", m.group(1))) if m else None


_LEFT = re.compile(r"^(?:(\d+)\s*\S*\s+)?(\d{1,2}):(\d\d):(\d\d)$")


def time_left(lines: list[str]) -> timedelta | None:
    for line in lines:
        m = _LEFT.match(line)
        if m:
            days, h, mi, s = (int(x) if x else 0 for x in m.groups())
            return timedelta(days=days, hours=h, minutes=mi, seconds=s)
    return None


def is_open_status(status: str) -> bool:
    return _clean(status).lower() in OPEN_STATUSES


def parse_card(section: str, r: dict, now: datetime) -> tuple[LotSummary, str] | None:
    """One list card → (summary, platform status). None for a malformed card."""
    lines = r.get("lines") or []
    if len(lines) < 3:
        return None
    status, title = lines[1], lines[2]
    org = region = None
    for i, line in enumerate(lines):
        if re.match(r"^(Ташкиллаштирувчи|Организатор|Tashkillashtiruvchi)\s*:", line):
            org = _clean(line.split(":", 1)[1]) or None
            region = lines[i + 1] if i + 1 < len(lines) else None
            break
    cost = next((parse_money(x) for x in lines if re.search(r"\d[\d\s]*\.\d\d\s*[A-Z]{3}$", x)), None)
    cur = next((x[-3:] for x in lines if re.search(r"\d\.\d\d\s*[A-Z]{3}$", x)), "UZS")
    left = time_left(lines)
    deadline = (now + left).replace(second=0, microsecond=0) if left else None
    title = _clean(title)
    return LotSummary(
        source="hayotbirja", external_id=str(r["id"]), url=lot_url(r["id"]), title=title,
        deadline_at=deadline, start_cost=cost, currency=cur, customer_name=org,
        customer_region=_clean(region) or None, lot_type=SECTIONS.get(section, section),
        # "ПГЗ из Агенства №…" says nothing: the stored lot takes the item names from its card instead
        title_is_category=bool(GENERIC.match(title)) or len(title) < 20,
    ), status


def _value_after(lines: list[str], i: int) -> str:
    """The value under a «Label :» line (blank lines skipped, the next label means no value)."""
    for line in lines[i + 1: i + 4]:
        line = _clean(line)
        if not line:
            continue
        return "" if line.endswith(":") else line
    return ""


def parse_detail(text: str) -> dict[str, Any]:
    """Card text (main tab + «Тижорат талаблари» tab) → items, names, customer, status."""
    lines = [x.rstrip() for x in (text or "").split("\n")]
    out: dict[str, Any] = {"items": [], "descs": [], "name": "", "basis": "", "customer": "", "status": ""}
    cur: dict[str, Any] | None = None
    in_positions = False
    for i, raw in enumerate(lines):
        line = _clean(raw)
        if not line:
            continue
        if line.startswith(("ҲОЛАТИ", "СОСТОЯНИЕ")) and not out["status"]:
            out["status"] = _value_after(lines, i)
        elif re.search(r"(буюртмачиси|Заказчик)\s*:$", line) and not out["customer"]:
            out["customer"] = _value_after(lines, i)
        elif re.search(r"(номи|Наименование процедуры)\s*:$", line) and not line.startswith("Номи") and not out["name"]:
            out["name"] = _value_after(lines, i)
        elif re.search(r"(асослари|учун асос)\s*:$", line) and not out["basis"]:
            out["basis"] = _value_after(lines, i)
        elif line == "Позициялар":
            in_positions = True
        elif re.match(r"^Позиция\s*[#№]\d+$", line):
            cur = {"n": "", "q": None, "u": "", "p": 0}
            out["items"].append(cur)
        elif line in ("Номи :", "Наименование :") and cur is not None and not cur["n"]:
            cur["n"] = _value_after(lines, i)
        elif line in ("Ўлчов бирлиги :", "Единица измерения :") and cur is not None:
            cur["u"] = _value_after(lines, i)
        elif line in ("Миқдор :", "Товар миқдори :", "Количество :") and cur is not None:
            cur["q"] = parse_money(_value_after(lines, i))
        elif re.search(r"бирлиги учун белгиланган нарх\s*:$", line) and cur is not None:
            cur["p"] = parse_money(_value_after(lines, i)) or 0
        elif line in ("Тавсиф :", "Описание :"):
            d = _value_after(lines, i)
            if d:
                out["descs"].append(d)
        elif in_positions and re.match(r"^#\d+$", line):
            # auction layout: «name», «#n», «code+unit»
            prev = next((_clean(x) for x in reversed(lines[max(0, i - 2): i]) if _clean(x)), "")
            if prev and not prev.startswith("#"):
                out["items"].append({"n": prev, "q": None, "u": "", "p": 0})
    out["items"] = [x for x in out["items"] if x["n"]]
    return out


def item_line(i: dict) -> str:
    qty = f"{i['q']:g} {i['u']}".strip() if i.get("q") else (i.get("u") or "")
    price = f" × {i['p']:,.0f} so'm".replace(",", " ") if i.get("p") else ""
    return f"- {i['n']}" + (f", {qty}" if qty else "") + price


def parse_lot(lot_id: str, d: dict, summary: LotSummary | None = None) -> LotRecord:
    text = d.get("main", "") + "\n" + (d.get("more") or "")
    info = parse_detail(text)
    items = info["items"]
    names = list(dict.fromkeys(i["n"] for i in items))
    lines = [item_line(i) for i in items]
    status = info["status"]
    description = "\n".join(p for p in [
        f"Jarayon nomi: {info['name']}" if info["name"] else "",
        f"Asos: {info['basis']}" if info["basis"] else "",
        "Pozitsiyalar:" if lines else "", *lines, *dict.fromkeys(info["descs"]),
    ] if p).strip()
    title = "; ".join(names)[:300] or info["name"] or (summary.title if summary else "") or f"Lot {lot_id}"
    total = sum((i["p"] or 0) * (i["q"] or 1) for i in items) or None
    return LotRecord(
        source="hayotbirja", external_id=str(lot_id), url=lot_url(lot_id), title=title,
        description=description,
        deadline_at=summary.deadline_at if summary else None,
        start_cost=(summary.start_cost if summary and summary.start_cost else total),
        currency=summary.currency if summary else "UZS",
        customer_name=info["customer"] or (summary.customer_name if summary else None),
        customer_region=summary.customer_region if summary else None,
        lot_type=summary.lot_type if summary else None,
        platform_status=status or None,
        is_open=is_open_status(status) if status else True,
        raw={"items": items},
    )


class HayotbirjaSource:
    name = "hayotbirja"
    title = "xt-xarid.uz (Hayot birja)"
    scan_descriptions = True
    phone_based = True
    max_details = 15  # cards per scan (each ~10 s of the phone + a pause for the site)

    def __init__(self, browser: PhoneBrowser, cache_dir: Path, *, sections: list[str] | None = None,
                 max_more: int = MAX_MORE):
        self.browser = browser
        self.cards = CardCache(cache_dir / "hayotbirja")
        self.state = JsonStore(cache_dir / "hayotbirja_state.json")
        self.sections = sections or list(SECTIONS)
        self.max_more = max_more
        self._listed: dict[str, LotSummary] = {}
        self._listed_at = 0.0     # monotonic time of the last full listing
        self._backoff_until = 0.0  # monotonic time: no requests to the site before this

    def _limited(self) -> bool:
        return time.monotonic() < self._backoff_until

    def _hit_limit(self) -> None:
        self._backoff_until = time.monotonic() + BACKOFF_S
        log.warning("hayotbirja.rate_limited", extra={"pause_h": BACKOFF_S / 3600})

    async def _pause(self) -> None:
        await asyncio.sleep(PAGE_PAUSE_S)

    async def _section(self, section: str) -> list[dict]:
        async with self.browser.page(f"{SITE}/procedure/{section}") as tab:
            await self.browser.wait_for(tab, f"({LIST_READY_JS}) || ({RATE_LIMIT_JS})", limit_s=PAGE_WAIT_S)
            if await tab.eval(RATE_LIMIT_JS):
                raise RateLimited("xt-xarid.uz: 429 — juda ko'p so'rov")
            for _ in range(self.max_more):
                before = await tab.eval(MORE_JS)
                if before is False:
                    break
                grew = await self.browser.wait_for(tab, f"({COUNT_JS}) > {int(before or 0)}", limit_s=30)
                if not grew:
                    break
                tail = (await tab.eval(LIST_JS) or [])[-50:]
                if tail and not any(is_open_status((r.get("lines") or ["", ""])[1]) for r in tail):
                    break  # newest first: the last 50 are all closed → the rest is history
            cards = await tab.eval(LIST_JS) or []
        await self._pause()
        if not cards and section not in MAY_BE_EMPTY:
            raise ValueError("ro'yxat bo'sh (sahifa vaqtida yuklanmadi)")
        return cards

    async def list_open(self) -> list[LotSummary]:
        """Open lots of every section. If a section will not load even on a second try, the whole listing
        fails: a partial list would drop that section's lots from the site until the next scan."""
        if self._listed and (time.monotonic() - self._listed_at < LIST_EVERY_S or self._limited()):
            return list(self._listed.values())  # read within the hour, or the site asked us to wait
        if self._limited():
            raise ValueError("xt-xarid.uz so'rovlarni vaqtincha cheklagan (429) — keyinroq o'qiladi")
        now = now_utc()
        out: dict[str, LotSummary] = {}
        todo, failed = list(self.sections), []
        for attempt in range(2):
            failed = []
            for section in todo:
                try:
                    cards = await self._section(section)
                except RateLimited:
                    self._hit_limit()
                    if self._listed:
                        return list(self._listed.values())
                    raise
                except Exception as e:
                    failed.append(section)
                    log.warning("hayotbirja.section_failed",
                                extra={"section": section, "attempt": attempt + 1, "err": f"{type(e).__name__}: {e}"[:200]})
                    continue
                for r in cards:
                    parsed = parse_card(section, r, now)
                    if parsed is None or not is_open_status(parsed[1]):
                        continue
                    lot = self._remembered(parsed[0], now)
                    out.setdefault(lot.external_id, lot)
            if not failed or len(failed) == len(todo):
                break  # all done, or nothing loaded at all: the site is down, do not hold the phone longer
            todo = failed
        if failed:
            raise ValueError(f"hayotbirja: bo'limlar ochilmadi ({', '.join(failed)})")
        self.state.save(keep=set(out) | {k for k, v in self.state.data.items() if v.get("seen", "") > (
            now - timedelta(days=3)).isoformat()})
        self._listed = out
        self._listed_at = time.monotonic()
        log.info("hayotbirja.list_open", extra={"count": len(out), "failed": failed})
        return list(out.values())

    @staticmethod
    def needs_detail(lot: LotSummary) -> bool:
        """Open the card only when the list title says nothing ("ПГЗ из Агенства №…", "Takliflar so‘rovi");
        a lot whose own name is in the list ("Труба водосточная", "Йўл тўсиқлари") is judged by that name,
        so the phone is spent on the cards that can still turn out to be IT. (IT-looking names are always
        opened — the scan does that.)"""
        return bool(GENERIC.match(lot.title)) or len(lot.title) < 20

    def _remembered(self, lot: LotSummary, now: datetime) -> LotSummary:
        st = self.state.data.setdefault(lot.external_id, {})
        st["seen"] = now.isoformat()
        if lot.deadline_at:
            old = datetime.fromisoformat(st["d"]) if st.get("d") else None
            if old and abs((old - lot.deadline_at).total_seconds()) <= DEADLINE_SLACK_S:
                lot.deadline_at = old
            else:
                st["d"] = lot.deadline_at.isoformat()
        elif st.get("d"):
            lot.deadline_at = datetime.fromisoformat(st["d"])
        if st.get("t") and (GENERIC.match(lot.title) or len(lot.title) < 20):
            lot.title = st["t"]  # item names read from the card earlier
            lot.title_is_category = False
        return lot

    async def get_lot(self, external_id: str) -> LotRecord:
        cached = self.cards.get(external_id)
        if cached is None:
            if self._limited():
                raise RateLimited("xt-xarid.uz: kutish (429)")
            cached = await self.browser.read(lot_url(external_id), DETAIL_READY_JS, DETAIL_JS, limit_s=PAGE_WAIT_S)
            await self._pause()
            if cached and re.search(r"(^|\D)429(\D|$)", (cached.get("main") or "")[:400]):
                self._hit_limit()
                raise RateLimited("xt-xarid.uz: 429 — juda ko'p so'rov")
            if not cached or not cached.get("main"):
                raise ValueError(f"hayotbirja {external_id}: karta o'qilmadi")
            self.cards.put(external_id, cached)
        rec = parse_lot(external_id, cached, self._listed.get(external_id))
        if rec.title and not GENERIC.match(rec.title):
            self.state.data.setdefault(external_id, {})["t"] = rec.title
            self.state.save()
        return rec

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        raise RuntimeError("hayotbirja fayllari faqat lot sahifasida")

    async def aclose(self) -> None:
        return None
