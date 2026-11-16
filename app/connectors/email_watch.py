"""Gmail (IMAP, read-only) watcher for platforms without a usable API: Upwork and Fiverr.

- Logs in with a Gmail *App Password* from .env (IMAP_USER / IMAP_APP_PASSWORD), never the real password.
- Opens INBOX read-only and fetches with BODY.PEEK, so nothing is marked as read or changed.
- Only mail from the configured sender domains is read; the last processed UID is kept in the DB.
- Each email is classified by Gemini (the email body is untrusted data, fenced off):
    job_alert          → JobListing(s) → the normal score / proposal / card pipeline
    message / offer /
    order / payment …  → a Telegram notification with the link
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import hashlib
import html
import imaplib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.base import JobListing
from app.db.models import KV
from app.llm.gemini import GeminiClient
from app.llm.safety import new_tag, untrusted_rule, wrap_untrusted

log = logging.getLogger(__name__)
KV_KEY = "email_watch:last_uid"
MAX_BODY_CHARS = 15_000


@dataclass
class RawEmail:
    uid: int
    sender: str
    subject: str
    date: datetime | None
    text: str
    links: list[str] = field(default_factory=list)


def _decode(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def parse_message(uid: int, raw_bytes: bytes) -> RawEmail:
    msg = email.message_from_bytes(raw_bytes)
    plain, rich = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", "ignore")
        (plain if ctype == "text/plain" else rich if ctype == "text/html" else []).append(text)
    html_body = "\n".join(rich)
    links = list(dict.fromkeys(re.findall(r'href=["\'](https?://[^"\']+)', html_body)))
    body = "\n".join(plain) if plain else html_to_text(html_body)
    if not links:
        links = list(dict.fromkeys(re.findall(r"https?://[^\s<>\"')]+", body)))
    try:
        date = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        date = None
    return RawEmail(
        uid=uid,
        sender=parseaddr(msg.get("From", ""))[1].lower(),
        subject=_decode(msg.get("Subject")),
        date=date,
        text=body[:MAX_BODY_CHARS],
        links=links[:60],
    )


class ImapFetcher:
    """Synchronous IMAP access (run it in a thread)."""

    def __init__(self, host: str, user: str, password: str, senders: list[str], lookback_days: int = 3):
        self.host, self.user, self.password = host, user, password
        self.senders = senders
        self.lookback_days = lookback_days

    def _search(self, imap: imaplib.IMAP4_SSL, last_uid: int | None) -> list[int]:
        uids: set[int] = set()
        since = (datetime.now(UTC) - timedelta(days=self.lookback_days)).strftime("%d-%b-%Y")
        for domain in self.senders:
            crit = ["FROM", f'"{domain}"', "SINCE", since]
            if last_uid:
                crit = ["UID", f"{last_uid + 1}:*", *crit]
            typ, data = imap.uid("SEARCH", *crit)
            if typ == "OK" and data and data[0]:
                uids.update(int(x) for x in data[0].split())
        return sorted(u for u in uids if not last_uid or u > last_uid)

    def fetch_since(self, last_uid: int | None, limit: int) -> list[RawEmail]:
        imap = imaplib.IMAP4_SSL(self.host, timeout=60)
        try:
            imap.login(self.user, self.password)
            imap.select("INBOX", readonly=True)
            out = []
            for uid in self._search(imap, last_uid)[-limit:]:
                typ, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if typ == "OK" and data and isinstance(data[0], tuple):
                    out.append(parse_message(uid, data[0][1]))
            return out
        finally:
            with contextlib.suppress(Exception):
                imap.logout()


# ── Gemini extraction ───────────────────────────────────────────────────────
class EmailJob(BaseModel):
    title: str
    url: str = Field(description="direct link to the job from the email, or empty")
    description: str = ""
    budget_min: float | None = None
    budget_max: float | None = None
    currency: str = "USD"
    budget_type: Literal["fixed", "hourly"] = "fixed"


class EmailExtraction(BaseModel):
    platform: Literal["upwork", "fiverr", "other"]
    kind: Literal["job_alert", "new_message", "offer_or_invite", "order", "contract_started",
                  "payment", "review", "account", "marketing", "other"]
    summary_uz: str = Field(description="1-2 sentences in Uzbek (Latin)")
    action_url: str = Field(description="the most relevant link from the email (message/offer/order), or empty")
    jobs: list[EmailJob] = Field(description="only for job_alert emails; one item per job in the email")


SYSTEM = """You read notification emails from freelance platforms for a freelancer and return JSON.
- {rule}
- kind=job_alert only when the email lists one or more jobs/projects/briefs the freelancer could apply to.
- For each job copy the title, the job link that appears in the email (do not invent URLs), budget if
  stated (numbers only), and a short description from the email.
- Newsletters, tips and promotions → kind=marketing. Security/login/verification → kind=account.
- summary_uz: what happened and what the freelancer should do, in Uzbek (Latin script)."""

NOTIFY_KINDS = {"new_message", "offer_or_invite", "order", "contract_started", "payment", "review"}
KIND_UZ = {
    "new_message": "💬 Yangi xabar", "offer_or_invite": "📨 Taklif / taklifnoma", "order": "🛒 Buyurtma",
    "contract_started": "🎉 Shartnoma boshlandi", "payment": "💰 To'lov", "review": "⭐ Sharh",
}

Notify = Callable[[str], Awaitable[None]]


def job_external_id(platform: str, job: EmailJob) -> str:
    m = re.search(r"~0?([0-9a-z]{10,})", job.url or "", re.I)  # Upwork: …/jobs/…_~01abcd…
    if m:
        return m.group(1).lower()
    return hashlib.sha1(f"{platform}|{job.url or job.title}".encode()).hexdigest()[:16]


class EmailWatchConnector:
    name = "email"
    title = "Gmail (Upwork, Fiverr)"
    can_submit = False

    def __init__(self, fetcher: ImapFetcher, gemini: GeminiClient, sessionmaker: async_sessionmaker,
                 notify: Notify | None = None, *, max_per_poll: int = 20):
        self.fetcher = fetcher
        self.gemini = gemini
        self.sessionmaker = sessionmaker
        self.notify = notify
        self.max_per_poll = max_per_poll

    async def _last_uid(self) -> int | None:
        async with self.sessionmaker() as s:
            row = await s.get(KV, KV_KEY)
            return (row.value or {}).get("uid") if row else None

    async def _save_uid(self, uid: int) -> None:
        async with self.sessionmaker() as s:
            row = await s.get(KV, KV_KEY)
            if row is None:
                s.add(KV(key=KV_KEY, value={"uid": uid}))
            else:
                row.value = {"uid": uid}
            await s.commit()

    async def extract(self, mail: RawEmail) -> EmailExtraction:
        tag = new_tag()
        body = "\n".join([
            f"From: {mail.sender}", f"Subject: {mail.subject}", "", mail.text, "",
            "Links in the email:", *mail.links,
        ])
        return await self.gemini.generate_json(
            wrap_untrusted("email", body, tag), EmailExtraction, role="fast",
            system=SYSTEM.format(rule=untrusted_rule(tag)), purpose="email_extract", temperature=0.1,
        )

    async def fetch_jobs(self) -> list[JobListing]:
        last = await self._last_uid()
        mails = await asyncio.to_thread(self.fetcher.fetch_since, last, self.max_per_poll)
        listings: list[JobListing] = []
        for mail in mails:
            try:
                ex = await self.extract(mail)
            except Exception:
                log.exception("email.extract_failed", extra={"uid": mail.uid})
                break  # retry this email next poll
            if ex.kind == "job_alert" and ex.platform in ("upwork", "fiverr"):
                for job in ex.jobs:
                    listings.append(JobListing(
                        platform=ex.platform, external_id=job_external_id(ex.platform, job),
                        url=job.url or (ex.action_url or ""), title=job.title[:300],
                        description=job.description, budget_min=job.budget_min, budget_max=job.budget_max,
                        currency=(job.currency or "USD").upper(), budget_type=job.budget_type,
                        posted_at=mail.date, language="en",
                        raw={"via": "email", "email_subject": mail.subject[:200]},
                    ))
            elif ex.kind in NOTIFY_KINDS and self.notify:
                link = f"\n🔗 {html.escape(ex.action_url)}" if ex.action_url else ""
                await self.notify(
                    f"📧 <b>{ex.platform.title()}</b> · {KIND_UZ.get(ex.kind, ex.kind)}\n"
                    f"{html.escape(ex.summary_uz)}\n<i>{html.escape(mail.subject[:150])}</i>{link}"
                )
            await self._save_uid(mail.uid)
        log.info("email.fetched", extra={"emails": len(mails), "jobs": len(listings)})
        return listings

    async def aclose(self) -> None:
        pass
