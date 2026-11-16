"""Kwork.ru «Биржа» — public projects page of the «Разработка и IT» category (c=11).

The page embeds its project list as JSON ("wants": [...]); we read only the first page, once per
poll (robots.txt forbids ?page= pagination, so we don't paginate). No login, no bidding.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.connectors.base import JobListing
from app.tenders.http import PoliteClient

log = logging.getLogger(__name__)

PAGE = "https://kwork.ru/projects"
IT_CATEGORY = 11
MSK = ZoneInfo("Europe/Moscow")


def extract_wants(html: str) -> list[dict]:
    i = html.find('"wants":')
    if i < 0:
        raise ValueError("Kwork page layout changed: no \"wants\" block")
    start = html.find("[", i)
    depth, in_str, esc = 0, False, False
    for j in range(start, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[start : j + 1])
    raise ValueError("Kwork page: unterminated wants JSON")


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_want(w: dict) -> JobListing:
    user = w.get("user") or {}
    udata = user.get("data") or {}
    created = w.get("date_create") or w.get("date_active")
    posted = datetime.fromisoformat(created).replace(tzinfo=MSK) if created else None
    hired = _float(udata.get("wants_hired_percent"))
    return JobListing(
        platform="kwork",
        external_id=str(w["id"]),
        url=f"https://kwork.ru/projects/{w['id']}",
        title=" ".join((w.get("name") or "").split()),
        description=" ".join((w.get("description") or "").split()),
        budget_min=None,
        budget_max=_float(w.get("priceLimit")),  # buyer's budget; possiblePriceLimit is the 3× offer cap
        currency="RUB",
        budget_type="fixed",
        proposals_count=int(w["kwork_count"]) if str(w.get("kwork_count", "")).isdigit() else None,
        posted_at=posted,
        language=w.get("lang") or "ru",
        skills=[],
        raw={
            "category_id": w.get("category_id"),
            "max_days": w.get("max_days"),
            "max_offer_price": _float(w.get("possiblePriceLimit")),
            "client": {
                "projects_posted": udata.get("wants_count"),
                "hired_percent": hired,
                "badges": [b.get("badge", {}).get("title") for b in user.get("badges") or [] if b.get("badge")],
            },
        },
    )


class KworkConnector:
    name = "kwork"
    title = "Kwork"
    can_submit = False

    def __init__(self, *, category: int = IT_CATEGORY, delay_s: float = 2.0, client: PoliteClient | None = None):
        self.category = category
        self.http = client or PoliteClient(delay_s=delay_s, headers={"Accept": "text/html"})

    async def fetch_jobs(self) -> list[JobListing]:
        resp = await self.http.request("GET", PAGE, params={"c": self.category})
        wants = extract_wants(resp.text)
        out = [parse_want(w) for w in wants if w.get("id") and w.get("status", "active") == "active"]
        log.info("kwork.fetched", extra={"count": len(out)})
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
