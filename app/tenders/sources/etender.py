"""etender.uzex.uz via its public JSON API (see docs/etender_api.md). Read-only: no login, no bids."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.tenders.http import PoliteClient
from app.tenders.schemas import Criterion, LotFile, LotRecord, LotSummary
from app.tenders.timeutil import parse_local

log = logging.getLogger(__name__)

API = "https://apietender.uzex.uz"
SITE = "https://etender.uzex.uz"
OPEN_STATUS_ID = 4  # "Eʼlon qilindi"
MAX_PAGES = 20

FILE_KINDS = [
    ("tech_file", "tz", "Texnik topshiriq"),
    ("tech_doc_file", "tech_doc", "Texnik hujjatlar"),
    ("add_file", "additional", "Qo'shimcha fayl"),
    ("contract_proform_file", "contract_draft", "Shartnoma loyihasi"),
    ("expertise_file", "expertise", "Ekspertiza xulosasi"),
]
PERIOD_UZ = {"Год (лет)": "yil", "Месяцев": "oy", "Месяц": "oy", "Дней": "kun", "День": "kun"}
LANG_CODE = {"Узбекский": "uz", "Русский": "ru", "Английский": "en", "English": "en"}


def lot_url(external_id: str | int) -> str:
    return f"{SITE}/lot/{external_id}"


def _json_field(value: Any) -> Any:
    """Several fields arrive as JSON *strings* (budget_products, languages, ...)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _clean(text: Any) -> str:
    return " ".join(str(text).split()) if text else ""


def parse_list_row(row: dict[str, Any]) -> LotSummary:
    return LotSummary(
        source="etender",
        external_id=str(row["id"]),
        url=lot_url(row["id"]),
        title=_clean(row.get("name")),
        deadline_at=parse_local(row.get("end_date")),
        start_cost=row.get("cost"),
        currency=row.get("currency_codeabc") or "UZS",
        customer_name=_clean(row.get("seller_name")) or None,
        customer_tin=row.get("seller_tin"),
        customer_region=row.get("region_name"),
        lot_type={1: "Eng yaxshi takliflarni tanlash", 2: "Tender"}.get(row.get("type_id")),
    )


def parse_trade(d: dict[str, Any]) -> LotRecord:
    products = _json_field(d.get("budget_products")) or []
    exec_days = [p.get("Delivery_Term") for p in products if p.get("Delivery_Term")]
    warranty = None
    for p in products:
        if p.get("Guarantee_Term"):
            unit = PERIOD_UZ.get(p.get("Term_Period_Name") or "", p.get("Term_Period_Name") or "")
            warranty = f"{p['Guarantee_Term']} {unit}".strip()
            break

    languages = [
        LANG_CODE.get(item.get("Name"), item.get("Name"))
        for item in (_json_field(d.get("languages")) or [])
        if isinstance(item, dict)
    ]

    files = []
    for prefix, kind, label in FILE_KINDS:
        path = d.get(f"{prefix}_path")
        if path:
            files.append(
                LotFile(
                    kind=kind,
                    label=label,
                    name=d.get(f"{prefix}_name"),
                    path=path,
                    ext=(d.get(f"{prefix}_ext") or "").lower() or None,
                    size=d.get(f"{prefix}_sizes"),
                )
            )

    criteria = [
        Criterion(
            label=_clean(c.get("label")),
            max_score=c.get("max_score"),
            required=c.get("is_required"),
            description=_clean(c.get("description")) or None,
        )
        for c in d.get("js_fields") or []
    ]
    qualification = [_clean(q.get("name")) for q in d.get("js_qualification_fields") or [] if q.get("name")]

    start_cost = d.get("start_cost")
    pledge_pct = d.get("pledge_value")
    pledge_amount = round(start_cost * pledge_pct / 100, 2) if start_cost and pledge_pct else None
    description = _clean(d.get("technical_description") or d.get("addon_description") or d.get("description"))
    product_names = "; ".join(
        dict.fromkeys(_clean(p.get("Product_Name") or p.get("Description")) for p in products if p)
    )
    title = description[:300] if description else product_names

    return LotRecord(
        source="etender",
        external_id=str(d["id"]),
        url=lot_url(d["id"]),
        title=title or f"Lot {d['id']}",
        description=description if not product_names else f"{description}\nPredmet: {product_names}",
        deadline_at=parse_local(d.get("end_date")),
        published_at=parse_local(d.get("start_date")),
        start_cost=start_cost,
        currency=d.get("currency_codeabc") or "UZS",
        customer_name=_clean(d.get("customer_name")) or None,
        customer_tin=d.get("customer_tin"),
        customer_region=d.get("customer_region_name"),
        lot_type=d.get("type_name"),
        pledge_percent=pledge_pct,
        pledge_amount=pledge_amount,
        advance_percent=d.get("advance_payment_perc"),
        payment_days=d.get("term_payment_days"),
        execution_days=max(exec_days) if exec_days else None,
        warranty=warranty,
        valuation_method=d.get("valuation_name"),
        price_weight=d.get("cost_coef"),
        tech_weight=d.get("tech_coef"),
        pass_mark=d.get("min_point"),
        criteria=criteria,
        qualification=qualification,
        languages=languages,
        financing_source=d.get("financing_source"),
        files=files,
        platform_status=d.get("status_name"),
        is_open=d.get("status_id") == OPEN_STATUS_ID,
        is_prolonged=bool(d.get("is_prolonged")),
        raw=d,
    )


class EtenderSource:
    name = "etender"
    title = "etender.uzex.uz"

    def __init__(self, *, delay_s: float = 2.5, page_size: int = 200, client: PoliteClient | None = None):
        self.page_size = page_size
        self.http = client or PoliteClient(
            delay_s=delay_s, headers={"Origin": SITE, "Referer": f"{SITE}/"}
        )

    async def list_open(self) -> list[LotSummary]:
        out: list[LotSummary] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES):
            start = page * self.page_size + 1
            rows = await self.http.post_json(
                f"{API}/api/common/TradeList",
                {"From": start, "To": start + self.page_size - 1, "System_Id": 0},
            )
            if not isinstance(rows, list):
                raise ValueError(f"TradeList returned {type(rows).__name__}, expected list")
            for row in rows:
                lot = parse_list_row(row)
                if lot.external_id not in seen:
                    seen.add(lot.external_id)
                    out.append(lot)
            total = rows[0].get("total_count", 0) if rows else 0
            if len(rows) < self.page_size or start + self.page_size > total:
                break
        log.info("etender.list_open", extra={"count": len(out)})
        return out

    async def get_lot(self, external_id: str) -> LotRecord:
        data = await self.http.get_json(f"{API}/api/common/GetTrade/{external_id}/0")
        if not isinstance(data, dict) or "id" not in data:
            raise ValueError(f"GetTrade/{external_id}: unexpected response")
        return parse_trade(data)

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        return await self.http.download(
            "POST",
            f"{API}/api/common/DownloadFile",
            dest,
            max_bytes=max_bytes,
            params={"path": file.path},
        )

    async def aclose(self) -> None:
        await self.http.aclose()
