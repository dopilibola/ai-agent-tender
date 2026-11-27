"""xarid.uzex.uz — «Tanlov» (competition) via its public JSON API. Read-only.

Endpoints (seen in the site's own requests, checked 27.11.2026, no login, no token):
  POST https://xarid-api-purchase.uzex.uz/Common/GetCompetitions   {"region_ids": [], "from": 1, "to": 200}
  GET  https://xarid-api-purchase.uzex.uz/Common/GetCompetition/<id>
The list is newest first and has only the product category, so — like «taklif so'rovi» — the keyword
filter runs on the category and cards are opened only for IT-looking categories.
Attachments are served by xarid-api-trade, which answers only a browser ("Unknown User-Agent"); the bot
does not pretend to be one, so file names go into the description and the lot page link is the way in.
The bot introduces itself by its own name (USER_AGENT below).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.tenders.http import PoliteClient
from app.tenders.schemas import LotFile, LotRecord, LotSummary
from app.tenders.timeutil import now_utc, parse_local

log = logging.getLogger(__name__)

API = "https://xarid-api-purchase.uzex.uz"
SITE = "https://xarid.uzex.uz"
USER_AGENT = "ai-agent-tender/1.0 (tender monitoring bot)"
LOT_TYPE = "Tanlov"
OPEN_STATUS = "Опубликован"
PAGE = 200
MAX_PAGES = 15


def lot_url(lot_id: int | str) -> str:
    return f"{SITE}/purchase/competition/detail/{lot_id}"


def _clean(v: Any) -> str:
    return " ".join(str(v).replace("\xa0", " ").split()) if v else ""


def _region(row: dict) -> str | None:
    parts = [_clean(row.get("customer_region_name")), _clean(row.get("customer_district_name"))]
    return ", ".join(p for p in parts if p) or None


def parse_list_row(row: dict) -> LotSummary:
    return LotSummary(
        source="xarid_tanlov",
        external_id=str(row["id"]),
        url=lot_url(row["id"]),
        title=_clean(row.get("category_name")) or f"Lot {row['id']}",
        deadline_at=parse_local(row.get("end_date_submitting_offers")),
        start_cost=row.get("cost"),
        currency=_clean(row.get("currency_name")) or "UZS",
        customer_region=_region(row),
        lot_type=LOT_TYPE,
        title_is_category=True,
    )


def items(d: dict) -> list[dict]:
    """Item lines in the shape app/tenders/prices.py reads from raw["items"]."""
    out = []
    for x in d.get("js_details") or []:
        unit = next((_clean(p.get("user_value")) for p in x.get("js_properties") or []
                     if isinstance(p, dict) and "измерения" in (p.get("property_name") or "")), "")
        out.append({"n": _clean(x.get("product_name")), "q": x.get("quantity"), "u": unit or _clean(x.get("unit_name")),
                    "p": x.get("price") or 0})
    return [i for i in out if i["n"]]


def parse_lot(d: dict) -> LotRecord:
    lines = []
    for x in d.get("js_details") or []:
        desc = _clean(x.get("description"))
        lines.append(f"- {_clean(x.get('product_name'))}, {x.get('quantity') or '?'} × {x.get('price') or '?'} = "
                     f"{x.get('cost') or '?'} so'm" + (f". {desc}" if desc else ""))
    names = list(dict.fromkeys(_clean(x.get("product_name")) for x in d.get("js_details") or []))
    names = [n for n in names if n]
    files = [_clean(f.get("custom_name") or f.get("file_name")) for f in d.get("js_files") or []]
    description = "\n".join(p for p in [
        _clean(d.get("competition_description")),
        f"To'lov sharti: {_clean(d.get('competition_bid_reg_form'))}" if d.get("competition_bid_reg_form") else "",
        f"Yetkazish manzili: {_clean(d.get('delivery_address'))}" if d.get("delivery_address") else "",
        f"Yetkazish muddati: {d.get('delivery_days')} kun" if d.get("delivery_days") else "",
        "Pozitsiyalar:", *lines,
        ("Fayllar (lot sahifasida): " + ", ".join(f for f in files if f)) if files else "",
    ] if p).strip()
    end = parse_local(d.get("end_date_submitting_offers"))
    return LotRecord(
        source="xarid_tanlov",
        external_id=str(d["id"]),
        url=lot_url(d["id"]),
        title="; ".join(names)[:300] or _clean(d.get("category_name")) or f"Lot {d['id']}",
        description=description,
        deadline_at=end,
        published_at=parse_local(d.get("published_date") or d.get("start_date_submitting_offers")),
        start_cost=d.get("cost"),
        currency=_clean(d.get("currency_name")) or "UZS",
        customer_name=_clean(d.get("customer_name")) or None,
        customer_tin=_clean(d.get("customer_inn")) or None,
        customer_region=_clean(d.get("customer_address")) or None,
        lot_type=LOT_TYPE,
        execution_days=d.get("delivery_days") or None,
        valuation_method="Tanlov (eng yaxshi taklif)",
        platform_status=_clean(d.get("status_name")) or None,
        is_open=_clean(d.get("status_name")) == OPEN_STATUS and (end is None or end > now_utc()),
        raw={"items": items(d), "id": d.get("id")},
    )


class XaridTanlovSource:
    name = "xarid_tanlov"
    title = "xarid.uzex.uz (tanlov)"
    scan_descriptions = False  # open cards only for IT categories

    def __init__(self, *, delay_s: float = 2.5, client: PoliteClient | None = None):
        self.http = client or PoliteClient(
            delay_s=delay_s, headers={"User-Agent": USER_AGENT, "Origin": SITE, "Referer": f"{SITE}/"})

    async def list_open(self) -> list[LotSummary]:
        out: dict[str, LotSummary] = {}
        now = now_utc()
        closed_pages = 0
        for page in range(MAX_PAGES):
            start = page * PAGE + 1
            rows = await self.http.post_json(f"{API}/Common/GetCompetitions",
                                             {"region_ids": [], "from": start, "to": start + PAGE - 1})
            if not isinstance(rows, list):
                raise ValueError("xarid GetCompetitions: expected a list")
            opened = 0
            for row in rows:
                lot = parse_list_row(row)
                if lot.deadline_at and lot.deadline_at <= now:
                    continue
                opened += 1
                out.setdefault(lot.external_id, lot)
            closed_pages = 0 if opened else closed_pages + 1
            total = rows[0].get("total_count", 0) if rows else 0
            # newest first: two pages in a row with nothing open → the rest is history
            if len(rows) < PAGE or start + PAGE > total or closed_pages >= 2:
                break
        log.info("xarid_tanlov.list_open", extra={"count": len(out)})
        return list(out.values())

    async def get_lot(self, external_id: str) -> LotRecord:
        data = await self.http.get_json(f"{API}/Common/GetCompetition/{external_id}")
        if not isinstance(data, dict) or "id" not in data:
            raise ValueError(f"xarid GetCompetition {external_id}: unexpected response")
        return parse_lot(data)

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        raise RuntimeError("xarid tanlov fayllari faqat lot sahifasida")  # no files are listed, see above

    async def aclose(self) -> None:
        await self.http.aclose()
