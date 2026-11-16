"""Freelancer.com official REST API (projects search works without a token; bidding needs one).

Docs: https://developers.freelancer.com — GET /api/projects/0.1/projects/active/
Skill ids come from GET /api/projects/0.1/jobs/ (looked up 17.11.2026).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.connectors.base import JobListing
from app.tenders.http import PoliteClient

log = logging.getLogger(__name__)

API = "https://www.freelancer.com/api/projects/0.1"
SITE = "https://www.freelancer.com"

# profile.yaml skills → Freelancer.com job (skill) ids.
# Not used on purpose: "CRM" (147) and "Automation" (1977) — on Freelancer.com they are mostly
# telemarketing / sales / engineering-simulation posts (checked 17.11.2026).
SKILL_IDS = {
    "Python": 13, "Django": 113, "FastAPI": 2688, "Flutter": 1315, "Telegram API": 2380, "n8n": 3112,
    "API Development": 2164, "REST API": 2703, "Web Scraping": 95, "Bot Development": 2918,
    "Chatbot": 2068, "LLMs": 2966, "OpenAI": 2719, "AI Development": 2986, "PostgreSQL": 607,
    "React.js": 759, "Make.com": 2823,
}


def parse_project(p: dict) -> JobListing:
    budget = p.get("budget") or {}
    currency = p.get("currency") or {}
    submitted = p.get("time_submitted")
    return JobListing(
        platform="freelancer_com",
        external_id=str(p["id"]),
        url=f"{SITE}/projects/{p.get('seo_url') or p['id']}",
        title=" ".join((p.get("title") or "").split()),
        description=(p.get("description") or p.get("preview_description") or "").strip(),
        budget_min=budget.get("minimum"),
        budget_max=budget.get("maximum"),
        currency=currency.get("code") or "USD",
        usd_rate=currency.get("exchange_rate"),
        budget_type="hourly" if p.get("type") == "hourly" else "fixed",
        proposals_count=(p.get("bid_stats") or {}).get("bid_count"),
        posted_at=datetime.fromtimestamp(submitted, UTC) if submitted else None,
        language=p.get("language"),
        skills=[j.get("name") for j in p.get("jobs") or [] if j.get("name")],
        raw={k: p.get(k) for k in ("id", "seo_url", "type", "budget", "currency", "bid_stats",
                                   "time_submitted", "language", "upgrades", "status")},
    )


class FreelancerComConnector:
    name = "freelancer_com"
    title = "Freelancer.com"

    def __init__(self, *, skill_ids: list[int] | None = None, token: str | None = None,
                 lookback_hours: int = 72, delay_s: float = 2.0, client: PoliteClient | None = None):
        self.skill_ids = skill_ids or sorted(set(SKILL_IDS.values()))
        self.lookback_hours = lookback_hours
        headers = {"freelancer-oauth-v1": token} if token else {}
        self.http = client or PoliteClient(delay_s=delay_s, headers=headers)
        self.can_submit = False  # bidding not implemented yet → manual paste after approval

    async def fetch_jobs(self) -> list[JobListing]:
        params: list[tuple[str, str | int]] = [
            ("limit", 100), ("full_description", "true"), ("job_details", "true"), ("compact", "true"),
            ("sort_field", "time_submitted"), ("from_time", int(time.time()) - self.lookback_hours * 3600),
        ] + [("jobs[]", i) for i in self.skill_ids]
        data = await self.http.get_json(f"{API}/projects/active/", params=params)
        if data.get("status") != "success":
            raise ValueError(f"Freelancer.com API: {data.get('message') or data.get('status')}")
        projects = (data.get("result") or {}).get("projects") or []
        out = [parse_project(p) for p in projects if p.get("frontend_project_status", "open") == "open"]
        log.info("freelancer_com.fetched", extra={"count": len(out)})
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
