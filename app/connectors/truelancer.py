"""Truelancer via a real browser (Playwright, persistent profile). Read-only.

- Public skill pages (/freelance-python-jobs …) embed their project list as Next.js JSON; we read it.
- robots.txt (checked 17.11.2026) allows everything. Plain HTTP clients get HTTP 429, so a normal
  browser is used — at human pace: one page every 20–40 s and a daily page cap.
- If a page shows a CAPTCHA, a login wall, "too many requests" or an error status, the connector
  STOPS for `block_pause_hours` and the owner gets one Telegram alert. No bypass attempts.
- The browser profile lives in data/browser_profile/truelancer. Logging in is optional for reading;
  do it once by hand:   .venv/bin/python -m app.connectors.truelancer login
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.connectors.base import JobListing

log = logging.getLogger(__name__)

SITE = "https://www.truelancer.com"
DEFAULT_SLUGS = ["freelance-python-jobs", "freelance-django-jobs", "freelance-flutter-jobs",
                 "freelance-api-development-jobs"]
BLOCK_MARKERS = ("captcha", "cf-challenge", "just a moment", "verify you are human", "too many requests",
                 "access denied", "unusual traffic")
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class TruelancerBlocked(RuntimeError):
    pass


def extract_projects(html: str) -> list[dict]:
    m = _NEXT.search(html)
    if not m:
        raise ValueError("Truelancer page layout changed: no __NEXT_DATA__")
    data = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("data") or {}
    projects = (data.get("projects") or {}).get("data")
    if projects is None:
        raise ValueError("Truelancer page layout changed: no projects list")
    return projects


def check_blocked(status: int | None, url: str, html: str) -> str | None:
    if status in (401, 403, 429, 503):
        return f"HTTP {status}"
    if "/login" in url or "/signin" in url:
        return "login sahifasiga yo'naltirdi"
    low = html[:200_000].lower()
    for marker in BLOCK_MARKERS:
        if marker in low and "__next_data__" not in low:
            return f"himoya sahifasi: «{marker}»"
    return None


def parse_project(p: dict) -> JobListing:
    jt = (p.get("displayjobtype") or {}).get("displayvalue") or ""
    created = p.get("created_at")
    country = p.get("countryObj") or {}
    return JobListing(
        platform="truelancer",
        external_id=str(p["id"]),
        url=p.get("link") or f"{SITE}/freelance-project/{p['id']}",
        title=" ".join((p.get("title") or "").split()),
        description=(p.get("description") or "").strip(),
        budget_max=float(p["budget"]) if p.get("budget") not in (None, "") else None,
        currency=(p.get("currency") or "USD").upper(),
        budget_type="hourly" if "hour" in jt.lower() else "fixed",
        proposals_count=p.get("total_proposals"),
        posted_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        language="en",
        skills=[s.get("tagName") or s.get("name") for s in p.get("skills") or [] if s],
        client_country=country.get("name") if isinstance(country, dict) else None,
        raw={"status": (p.get("publicStatusName") or {}).get("displayvalue"),
             "already_applied": p.get("alreadyApplied"), "category_id": p.get("category_id")},
    )


class TruelancerConnector:
    name = "truelancer"
    title = "Truelancer"
    can_submit = False

    def __init__(self, profile_dir: Path, *, slugs: list[str] | None = None, min_delay_s: int = 20,
                 max_delay_s: int = 40, daily_cap: int = 120, block_pause_hours: int = 6, headless: bool = True):
        self.profile_dir = profile_dir
        self.slugs = slugs or DEFAULT_SLUGS
        self.min_delay_s, self.max_delay_s = min_delay_s, max_delay_s
        self.daily_cap = daily_cap
        self.block_pause = timedelta(hours=block_pause_hours)
        self.headless = headless
        self.blocked_until: datetime | None = None
        self._day: date | None = None
        self._pages_today = 0

    def _budget_left(self) -> int:
        today = datetime.now(UTC).date()
        if self._day != today:
            self._day, self._pages_today = today, 0
        return self.daily_cap - self._pages_today

    async def fetch_jobs(self) -> list[JobListing]:
        now = datetime.now(UTC)
        if self.blocked_until and now < self.blocked_until:
            log.info("truelancer.paused", extra={"until": self.blocked_until.isoformat()})
            return []
        if self._budget_left() <= 0:
            log.info("truelancer.daily_cap")
            return []
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        out: dict[str, JobListing] = {}
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=self.headless, locale="en-US",
                viewport={"width": 1366, "height": 860},
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                for i, slug in enumerate(self.slugs):
                    if self._budget_left() <= 0:
                        break
                    if i:
                        await asyncio.sleep(random.uniform(self.min_delay_s, self.max_delay_s))
                    resp = await page.goto(f"{SITE}/{slug}", wait_until="domcontentloaded", timeout=60_000)
                    self._pages_today += 1
                    html = await page.content()
                    reason = check_blocked(resp.status if resp else None, page.url, html)
                    if reason:
                        self.blocked_until = datetime.now(UTC) + self.block_pause
                        raise TruelancerBlocked(
                            f"{reason} — Truelancer {int(self.block_pause.total_seconds() // 3600)} soatga "
                            "to'xtatildi. Brauzerda o'zingiz tekshirib ko'ring (login: "
                            "python -m app.connectors.truelancer login)."
                        )
                    for p in extract_projects(html):
                        if (p.get("publicStatusName") or {}).get("displayvalue", "Open") == "Open":
                            job = parse_project(p)
                            out.setdefault(job.external_id, job)
            finally:
                await ctx.close()
        log.info("truelancer.fetched", extra={"count": len(out), "pages_today": self._pages_today})
        return list(out.values())

    async def aclose(self) -> None:
        pass


async def _login(profile_dir: Path) -> None:
    """Open a visible browser with the bot's profile so I can log in by hand once."""
    from playwright.async_api import async_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - one-off CLI
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(profile_dir), headless=False, locale="en-US")
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(f"{SITE}/login")
        print("Brauzerda Truelancer'ga o'zingiz kiring. Tugagach, brauzer oynasini yoping.")
        closed = asyncio.Event()
        ctx.on("close", lambda *_: closed.set())
        await closed.wait()
    print("Saqlandi:", profile_dir)


if __name__ == "__main__":  # python -m app.connectors.truelancer login
    from app.config import get_settings

    if sys.argv[1:] == ["login"]:
        asyncio.run(_login(get_settings().secrets.data_dir / "browser_profile" / "truelancer"))
    else:
        print("Foydalanish: python -m app.connectors.truelancer login")
