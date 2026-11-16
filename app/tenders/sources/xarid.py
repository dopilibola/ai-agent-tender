"""xarid.uzex.uz — «Taklif so'rovi» (request for proposals) via its public JSON API. Read-only.

Endpoints (found in the site bundle, checked 17.11.2026, no login needed):
  POST https://xarid-api-prequest.uzex.uz/api/Public/GetLots      {"from": 1, "to": 300}
  GET  https://xarid-api-prequest.uzex.uz/api/Public/GetLot?id=…
  GET  https://xarid-api-prequest.uzex.uz/api/Files/GetLotFileById?id=…
The list has no item names — only the product category — so the keyword filter runs on the
category first and lot cards are opened only for IT-looking categories (scan_descriptions=False).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.tenders.http import PoliteClient
from app.tenders.schemas import LotFile, LotRecord, LotSummary
from app.tenders.timeutil import parse_local

log = logging.getLogger(__name__)

API = "https://xarid-api-prequest.uzex.uz"
SITE = "https://xarid.uzex.uz"
OPEN_STATUS_ID = 2  # «Опубликован»
MAX_PAGES = 10
LOT_TYPE = "Taklif so'rovi"
CURRENCY = {106: "UZS", "Сум": "UZS", "So'm": "UZS"}


def lot_url(lot_id: int | str) -> str:
    return f"{SITE}/request-proposal/3/detail/{lot_id}"


def _data(payload: Any) -> Any:
    if isinstance(payload, dict) and "Data" in payload:
        status = payload.get("Status")
        if status not in (None, 200):
            raise ValueError(f"xarid API status {status}: {payload.get('message')}")
        return payload["Data"]
    return payload


def _clean(v: Any) -> str:
    return " ".join(str(v).replace("\xa0", " ").split()) if v else ""


def parse_list_row(row: dict) -> LotSummary:
    return LotSummary(
        source="xarid",
        external_id=str(row["id"]),
        url=lot_url(row["id"]),
        title=_clean(row.get("categoryName")) or f"Lot {row['id']}",  # names only in the card
        deadline_at=parse_local(row.get("endDate")),
        start_cost=row.get("startCost"),
        currency=CURRENCY.get(row.get("currencyName"), "UZS"),
        customer_name=_clean(row.get("customerName")) or None,
        customer_tin=row.get("customerInn"),
        customer_region=_clean(row.get("territorialName") or row.get("address")) or None,
        lot_type=LOT_TYPE,
        title_is_category=True,
    )


def parse_lot(d: dict) -> LotRecord:
    details = d.get("details") or []
    names = list(dict.fromkeys(_clean(x.get("productName") or x.get("description")) for x in details))
    names = [n for n in names if n]
    lines = []
    for x in details:
        props = "; ".join(
            f"{_clean(p.get('propertyName') or p.get('name'))}: {_clean(p.get('value') or p.get('propertyValue'))}"
            for p in x.get("properties") or [] if isinstance(p, dict)
        )
        lines.append(
            f"- {_clean(x.get('productName'))} ({_clean(x.get('categoryName'))}), "
            f"{x.get('quantity') or '?'} × {x.get('price') or '?'} = {x.get('cost') or '?'} so'm"
            + (f". {_clean(x.get('description'))}" if _clean(x.get('description')) != _clean(x.get('productName')) else "")
            + (f". {props}" if props else "")
        )
    start_cost = d.get("startCost")
    advance = d.get("advanceCost")
    files = [
        LotFile(
            kind="tz" if i == 0 else "additional",
            label="Lot fayli" if i == 0 else f"Lot fayli {i + 1}",
            name=f.get("name"),
            path=str(f.get("id")),
            ext=(Path(f.get("name") or "").suffix.lstrip(".").lower() or None),
        )
        for i, f in enumerate(f for f in d.get("files") or [] if f.get("groupId", 1) == 1)
    ]
    description = "\n".join(
        [f"Asos: {_clean(d.get('decreeName'))}" if d.get("decreeName") else "",
         f"Yetkazish manzili: {_clean(d.get('deliveryAddress'))}" if d.get("deliveryAddress") else "",
         f"Avans to'lov muddati: {d.get('advanceTerm')} kun" if d.get("advanceTerm") else "",
         "Pozitsiyalar:", *lines]
    ).strip()
    return LotRecord(
        source="xarid",
        external_id=str(d["id"]),
        url=lot_url(d["id"]),
        title="; ".join(names)[:300] or _clean(d.get("categoryName")) or f"Lot {d['id']}",
        description=description,
        deadline_at=parse_local(d.get("endDate")),
        published_at=parse_local(d.get("startDate")),
        start_cost=start_cost,
        currency=CURRENCY.get(d.get("currencyId"), CURRENCY.get(d.get("currencyName"), "UZS")),
        customer_name=_clean(d.get("customerName")) or None,
        customer_tin=d.get("customerInn"),
        customer_region=_clean(d.get("territorialName") or d.get("address")) or None,
        lot_type=LOT_TYPE,
        advance_percent=round(advance / start_cost * 100, 1) if advance and start_cost else None,
        valuation_method="Eng past narx (taklif so'rovi)",
        price_weight=100.0,
        tech_weight=0.0,
        files=files,
        platform_status=d.get("statusName"),
        is_open=d.get("statusId") == OPEN_STATUS_ID,
        raw=d,
    )


class XaridSource:
    name = "xarid"
    title = "xarid.uzex.uz (taklif so'rovi)"
    scan_descriptions = False  # 1000+ open lots; open cards only for IT categories

    def __init__(self, *, delay_s: float = 2.5, page_size: int = 300, client: PoliteClient | None = None):
        self.page_size = page_size
        self.http = client or PoliteClient(delay_s=delay_s, headers={"Origin": SITE, "Referer": f"{SITE}/"})

    async def list_open(self) -> list[LotSummary]:
        out: dict[str, LotSummary] = {}
        for page in range(MAX_PAGES):
            start = page * self.page_size + 1
            rows = _data(await self.http.post_json(
                f"{API}/api/Public/GetLots", {"from": start, "to": start + self.page_size - 1}
            ))
            if not isinstance(rows, list):
                raise ValueError("xarid GetLots: expected a list")
            for row in rows:
                lot = parse_list_row(row)
                out.setdefault(lot.external_id, lot)
            total = rows[0].get("totalCount", 0) if rows else 0
            if len(rows) < self.page_size or start + self.page_size > total:
                break
        log.info("xarid.list_open", extra={"count": len(out)})
        return list(out.values())

    async def get_lot(self, external_id: str) -> LotRecord:
        data = _data(await self.http.get_json(f"{API}/api/Public/GetLot", params={"id": external_id}))
        if not isinstance(data, dict) or "id" not in data:
            raise ValueError(f"xarid GetLot {external_id}: unexpected response")
        return parse_lot(data)

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        return await self.http.download(
            "GET", f"{API}/api/Files/GetLotFileById", dest, max_bytes=max_bytes, params={"id": file.path}
        )

    async def aclose(self) -> None:
        await self.http.aclose()
