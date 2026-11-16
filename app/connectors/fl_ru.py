"""FL.ru via its public RSS feed for the «Программирование» category (one request per poll).

robots.txt (checked 17.11.2026) does not forbid /rss/ for generic user agents — only for search
crawlers (Yandex/Google indexing). Bidding on FL.ru is manual: approved text is pasted by me.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from app.connectors.base import JobListing
from app.tenders.http import PoliteClient

log = logging.getLogger(__name__)

FEED = "https://www.fl.ru/rss/all.xml"
PROGRAMMING_CATEGORY = 5

_BUDGET = re.compile(r"\(\s*Бюджет:\s*([\d\s ]+)\s*(&#8381;|₽|руб\.?|\$|€|&#36;)\s*\)", re.I)
_TAGS = re.compile(r"\s*\((для всех|только для PRO|вакансия)[^)]*\)\s*", re.I)
_CURRENCY = {"&#8381;": "RUB", "₽": "RUB", "руб": "RUB", "руб.": "RUB", "$": "USD", "&#36;": "USD", "€": "EUR"}
_ID = re.compile(r"/projects/(\d+)/")


def parse_item(item: ET.Element) -> JobListing | None:
    link = (item.findtext("link") or "").strip()
    m = _ID.search(link)
    if not m:
        return None
    raw_title = item.findtext("title") or ""
    budget = currency = None
    b = _BUDGET.search(raw_title)
    if b:
        budget = float(re.sub(r"\D", "", b.group(1)) or 0) or None
        currency = _CURRENCY.get(b.group(2).lower(), "RUB")
    title = _TAGS.sub(" ", _BUDGET.sub("", raw_title)).strip()
    desc = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
    pub = item.findtext("pubDate")
    category = item.findtext("category") or ""
    return JobListing(
        platform="fl_ru",
        external_id=m.group(1),
        url=link,
        title=html.unescape(" ".join(title.split())),
        description=" ".join(desc.split()),
        budget_min=None,
        budget_max=budget,
        currency=currency or "RUB",
        budget_type="fixed",
        proposals_count=None,  # not in the feed
        posted_at=parsedate_to_datetime(pub) if pub else None,
        language="ru",
        skills=[c.strip() for c in category.split("/") if c.strip()],
        raw={"category": category, "only_pro": "только для PRO" in raw_title},
    )


class FlRuConnector:
    name = "fl_ru"
    title = "FL.ru"
    can_submit = False

    def __init__(self, *, category: int = PROGRAMMING_CATEGORY, delay_s: float = 2.0, client: PoliteClient | None = None):
        self.category = category
        self.http = client or PoliteClient(delay_s=delay_s, headers={"Accept": "application/rss+xml, application/xml"})

    async def fetch_jobs(self) -> list[JobListing]:
        resp = await self.http.request("GET", FEED, params={"category": self.category})
        root = ET.fromstring(resp.content)
        items = [parse_item(i) for i in root.findall(".//item")]
        out = [i for i in items if i is not None]
        log.info("fl_ru.fetched", extra={"count": len(out)})
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
