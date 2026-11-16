"""Telegram job card (Uzbek, HTML parse mode)."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape as _escape

from app.db.models import Job, Proposal


def escape(s: str) -> str:
    return _escape(s, quote=False)


CURRENCY_SYMBOL = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "RUB": "₽", "UZS": "so'm "}

RISK_UZ = {
    "unrealistic_budget": "byudjet real emas",
    "vague_scope": "vazifa noaniq",
    "new_client_no_spend": "yangi mijoz, xarajat yo'q",
    "off_platform_payment": "platformadan tashqari to'lov",
    "academic_cheating": "akademik firibgarlik",
    "client_unknown": "mijoz ma'lumoti yo'q",
    "crowded": "raqobat ko'p",
    "skill_mismatch": "ko'nikma mos emas",
    "fake_reviews": "soxta sharh",
    "not_software": "IT emas",
    "long_term_fulltime": "doimiy ish",
    "needs_onsite": "joyida ishlash",
    "illegal": "noqonuniy",
}


def money(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "?"
    cur = (currency or "USD").upper()
    num = f"{amount:,.0f}" if amount >= 100 else f"{amount:,.2f}".rstrip("0").rstrip(".")
    sym = CURRENCY_SYMBOL.get(cur)
    return f"{sym}{num}" if sym else f"{num} {cur}"


def ago_uz(when: datetime | None, now: datetime | None = None) -> str:
    if when is None:
        return "vaqti noma'lum"
    now = now or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    minutes = max(0, int((now - when).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} daqiqa oldin"
    if minutes < 48 * 60:
        return f"{minutes // 60} soat oldin"
    return f"{minutes // 1440} kun oldin"


def fit_dot(fit: int | None) -> str:
    if fit is None:
        return "⚪️"
    return "🟢" if fit >= 80 else "🟡" if fit >= 60 else "🔴"


PLATFORM_NAME = {"freelancer_com": "Freelancer.com", "fl_ru": "FL.ru", "kwork": "Kwork",
                 "truelancer": "Truelancer", "upwork": "Upwork (email)", "fiverr": "Fiverr (email)"}


def format_job_card(job: Job, proposal: Proposal | None = None, now: datetime | None = None) -> str:
    hourly = job.budget_type == "hourly"
    budget = money(job.budget_max or job.budget_min, job.currency)
    if job.budget_max is None and job.budget_min is None:
        budget = "ko'rsatilmagan"
    if job.budget_min and job.budget_max and job.budget_min != job.budget_max:
        budget = f"{money(job.budget_min, job.currency)}–{money(job.budget_max, job.currency)}"
    if hourly:
        budget += "/soat"
    if job.budget_usd is not None and (job.currency or "USD").upper() != "USD":
        budget += f" (~${job.budget_usd:,.0f}{'/soat' if hourly else ''})"
    risks = ", ".join(RISK_UZ.get(r, r) for r in (job.risk_flags or [])) or "yo'q"
    win = f"{job.win_probability}%" if job.win_probability is not None else "?"
    platform = PLATFORM_NAME.get(job.platform, job.platform.title())
    lines = [
        f"💼 {fit_dot(job.fit)} <b>Yangi ish</b> | {escape(platform)} | "
        f"Fit {job.fit if job.fit is not None else '?'} · Yutish {win}",
        f"<b>\"{escape(job.title)}\"</b> — byudjet {escape(budget)} · "
        f"{job.proposals_count if job.proposals_count is not None else '?'} ta taklif · "
        f"{ago_uz(job.posted_at, now)}",
        f"<b>Xulosa:</b> {escape(job.summary_uz or '—')}",
        f"<b>Xavf:</b> {escape(risks)}",
    ]
    if proposal:
        parts = [f"Narxim: {money(proposal.price, proposal.currency)}{'/soat' if hourly else ''}"]
        if proposal.deposit:
            parts.append(f"Avans {money(proposal.deposit, proposal.currency)}")
        if proposal.timeline_days:
            parts.append(f"Muddat {proposal.timeline_days} kun")
        lines.append("<b>" + escape(" · ".join(parts)) + "</b>")
        lines.append("")
        lines.append(f"<blockquote expandable>{escape(proposal.text)}</blockquote>")
    return "\n".join(lines)
