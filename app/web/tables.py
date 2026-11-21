"""CRM-style sortable tables (tenders / freelance jobs) and the same rows for the Excel export.

Each column knows how to sort (key), how to show (one-line HTML cell) and what goes into Excel (plain value).
Empty values always sort to the bottom, in both directions. Rows are selectable (checkboxes → bulk actions)
and clickable (opens the detail page).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.bot.cards import RISK_UZ, ago_uz
from app.core.states import STATUS_LABEL_UZ, JobStatus
from app.db.models import Job, Lot, LotStatus
from app.tenders import cards as tc
from app.tenders.timeutil import as_utc, fmt_local, now_utc, remaining_uz
from app.web import views
from app.web.views import Ctx, ext_link, h

VERDICT_RANK = {"QATNASHAMIZ": 3, "SHARTLI": 2, "YO'Q": 1}
_SVG = ('<svg class="si" width="10" height="14" viewBox="0 0 10 14" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="{d}"/></svg>')
SORT_ICON = _SVG.format(d="M2 5l3-3 3 3M2 9l3 3 3-3")
SORT_UP = _SVG.format(d="M2 8l3-3 3 3")
SORT_DOWN = _SVG.format(d="M2 6l3 3 3-3")


@dataclass
class Col:
    key: str
    label: str
    sort: Callable[[Any], Any] | None  # None → not sortable
    cell: Callable[[Ctx, Any, datetime], str]
    excel: Callable[[Any], Any] | None = None  # None → not exported
    first_dir: str = "asc"  # direction on the first click
    width: int = 14  # Excel column width
    cls: str = ""  # wide | mid | num | dim | hidden (Excel only)


def sort_rows(rows: list, cols: list[Col], key: str, direction: str) -> list:
    col = next((c for c in cols if c.key == key and c.sort), None)
    if col is None:
        return rows
    present = [(col.sort(r), r) for r in rows]
    have = [x for x in present if x[0] is not None]
    missing = [r for v, r in present if v is None]
    have.sort(key=lambda x: x[0], reverse=direction == "desc")
    return [r for _, r in have] + missing


def _t(text: str | None, limit: int) -> str:
    """One-line cell text with the full value as a tooltip."""
    text = " ".join((text or "").split())
    if not text:
        return '<span class="muted">—</span>'
    return f'<span title="{h(text)}">{h(tc.short(text, limit))}</span>'


def _dt(dt: datetime | None) -> str:
    return h(fmt_local(dt)) if dt else '<span class="muted">—</span>'


# ── Tenders ─────────────────────────────────────────────────────────────────
def _a(lot: Lot) -> dict:
    return lot.analysis or {}


def _usd_cost(lot: Lot) -> float | None:
    return tc.usd(lot.start_cost, lot.currency, lot.usd_rate)


def pledge_value_usd(lot: Lot) -> float | None:
    """Zakalat in USD: 0 when the lot has no deposit, None when it exists but can't be converted."""
    if not lot.pledge_percent:
        return 0.0
    return tc.pledge_usd(lot)


def _pledge_cell(lot: Lot) -> str:
    if not lot.pledge_percent:
        return '<span class="muted">yo\'q</span>'
    usd = tc.pledge_usd(lot)
    return (f'<span title="{h(f"{lot.pledge_percent:g}% = " + tc.money_uzs(lot.pledge_amount, lot.currency))}">'
            f'{h(f"${usd:,.0f}".replace(",", " ") if usd is not None else tc.money_uzs(lot.pledge_amount, lot.currency))}'
            "</span>")


def _win(lot: Lot) -> int | None:
    w = _a(lot).get("win_chance_percent") or []
    return w[0] if len(w) == 2 else None


def _expired(lot: Lot, now: datetime) -> bool:
    return bool(lot.deadline_at and as_utc(lot.deadline_at) <= now)


def lot_status_pill(lot: Lot, now: datetime | None = None) -> str:
    now = now or now_utc()
    if lot.status == LotStatus.CLOSED or (_expired(lot, now) and lot.status != LotStatus.IGNORED):
        label = "Muddati o'tgan" if _expired(lot, now) else "Yopilgan"
        return f'<span class="pill solid-bad">{h(label)}</span>'
    cls, label = {
        LotStatus.NEW: ("blue", "Yangi"),
        LotStatus.NOTIFIED: ("blue", "Xabar qilingan"),
        LotStatus.BIDDING: ("good", "Qatnashyapmiz"),
        LotStatus.IGNORED: ("", "Kerak emas"),
    }.get(lot.status, ("", str(lot.status)))
    return f'<span class="pill {cls}">{h(label)}</span>'


def _verdict_pill(lot: Lot) -> str:
    a = _a(lot)
    if not a:
        return '<span class="pill">Kutilmoqda</span>'
    if a.get("error"):
        return '<span class="pill">Baholanmagan</span>'
    v = lot.verdict or "?"
    cls = {"QATNASHAMIZ": "good", "SHARTLI": "maybe", "YO'Q": "bad"}.get(v, "")
    label = {"QATNASHAMIZ": "Qatnashamiz", "SHARTLI": "Shartli", "YO'Q": "Yo'q"}.get(v, v)
    return f'<span class="pill {cls}">{h(label)}</span>'


def _lot_reason_text(lot: Lot) -> str:
    a = _a(lot)
    out = [a.get("verdict_reason_uz") or ""]
    if a.get("blockers"):
        out.append("To'siqlar: " + "; ".join(a["blockers"][:5]))
    if a.get("risks"):
        out.append("Risklar: " + "; ".join(a["risks"][:5]))
    return "\n".join(x for x in out if x)


def _deadline_cell(_ctx: Ctx, lot: Lot, now: datetime) -> str:
    if not lot.deadline_at:
        return '<span class="muted">—</span>'
    left = (as_utc(lot.deadline_at) - now).total_seconds()  # type: ignore[operator]
    cls = "urgent" if left < 86400 else "soon" if left < 3 * 86400 else ""
    return (f'<span class="{cls}" title="{h(remaining_uz(lot.deadline_at, now))}">'
            f'{h(fmt_local(lot.deadline_at))}</span>')


def _lot_no_sort(lot: Lot):
    return int(lot.external_id) if lot.external_id.isdigit() else None


def _lot_price(lot: Lot) -> str:
    usd = _usd_cost(lot)
    return f'<span title="{h(tc.fmt_usd(usd))}">{h(tc.money_uzs(lot.start_cost, lot.currency))}</span>'


def _lot_reason_cell(lot: Lot) -> str:
    return '<span class="muted">—</span>' if _a(lot).get("error") else _t(_lot_reason_text(lot), 55)


LOT_COLS: list[Col] = [
    Col("title", "Tavsif", lambda lot: (_a(lot).get("summary_uz") or lot.title).lower(),
        lambda c, lot, n: _t(_a(lot).get("summary_uz") or lot.title, 60),
        lambda lot: _a(lot).get("summary_uz") or lot.title, "asc", 60, "wide"),
    Col("no", "Lot №", _lot_no_sort,
        lambda c, lot, n: f'<span class="lotno">{ext_link(lot.url, lot.external_id)}</span>',
        lambda lot: lot.external_id, "desc", 12),
    Col("source", "Turi", lambda lot: f"{lot.source} {lot.lot_type or ''}".lower(),
        lambda c, lot, n: _t(f"{tc.SOURCE_NAME.get(lot.source, lot.source)} · {lot.lot_type or '?'}", 32),
        lambda lot: f"{tc.SOURCE_NAME.get(lot.source, lot.source)} · {lot.lot_type or ''}", "asc", 22, "mid"),
    Col("deadline", "Muddat", lambda lot: as_utc(lot.deadline_at) if lot.deadline_at else None,
        _deadline_cell, lambda lot: fmt_local(lot.deadline_at) if lot.deadline_at else "", "asc", 18),
    Col("customer", "Buyurtmachi", lambda lot: (lot.customer_name or "").lower() or None,
        lambda c, lot, n: _t(lot.customer_name, 34), lambda lot: lot.customer_name or "", "asc", 35, "mid"),
    Col("price", "Narx", lambda lot: _usd_cost(lot) if _usd_cost(lot) is not None else lot.start_cost,
        lambda c, lot, n: _lot_price(lot), lambda lot: lot.start_cost, "desc", 16, "num"),
    Col("pledge", "Zakalat", pledge_value_usd, lambda c, lot, n: _pledge_cell(lot), pledge_value_usd, "asc", 12, "num"),
    Col("verdict", "AI xulosa", lambda lot: VERDICT_RANK.get(lot.verdict or ""),
        lambda c, lot, n: _verdict_pill(lot), lambda lot: lot.verdict or "", "desc", 14),
    Col("fit", "Fit", lambda lot: _a(lot).get("fit_score"),
        lambda c, lot, n: h(_a(lot).get("fit_score")) if _a(lot).get("fit_score") is not None else "—",
        lambda lot: _a(lot).get("fit_score"), "desc", 6, "num"),
    Col("win", "Yutish %", _win,
        lambda c, lot, n: h("–".join(map(str, _a(lot).get("win_chance_percent") or []))) or "—",
        lambda lot: "–".join(map(str, _a(lot).get("win_chance_percent") or [])), "desc", 9, "num"),
    Col("reason", "AI izohi", None, lambda c, lot, n: _lot_reason_cell(lot), _lot_reason_text, width=70,
        cls="wide"),
    Col("status", "Holat", lambda lot: ("z" if _expired(lot, now_utc()) else "") + str(lot.status),
        lambda c, lot, n: lot_status_pill(lot, n),
        lambda lot: tc.STATUS_UZ.get(lot.status, str(lot.status)), "asc", 16),
    Col("updated", "O'zgartirilgan sana", lambda lot: as_utc(lot.updated_at) if lot.updated_at else None,
        lambda c, lot, n: _dt(lot.updated_at), lambda lot: fmt_local(lot.updated_at), "desc", 18, "dim"),
    Col("url", "Havola", None, lambda c, lot, n: "", lambda lot: lot.url, width=40, cls="hidden"),
]


# ── Freelance jobs ──────────────────────────────────────────────────────────
def _job_usd(job: Job) -> float | None:
    if job.budget_usd is not None:
        return job.budget_usd
    return job.budget_max or job.budget_min if (job.currency or "USD").upper() == "USD" else None


def _fit_pill(job: Job) -> str:
    if job.fit is None:
        return '<span class="muted">—</span>'
    cls = "good" if job.fit >= 80 else "maybe" if job.fit >= 60 else "bad"
    return f'<span class="pill {cls}">{job.fit}</span>'


S = JobStatus
JOB_PILL = {
    S.AWAITING_APPROVAL: "maybe", S.DRAFTED: "maybe", S.SCORED: "blue", S.FOUND: "blue", S.APPROVED: "blue",
    S.SUBMITTED: "blue", S.CLIENT_REPLIED: "good", S.INTERVIEW: "good", S.HIRED: "good", S.IN_PROGRESS: "good",
    S.REVIEW_READY: "good", S.DELIVERED: "good", S.PAID: "good", S.REJECTED: "solid-bad", S.EXPIRED: "solid-bad",
    S.ERROR: "bad", S.SKIPPED: "",
}


def job_status_pill(job: Job) -> str:
    label = STATUS_LABEL_UZ.get(job.status, str(job.status)).split(" ", 1)[-1]
    return f'<span class="pill {JOB_PILL.get(job.status, "")}">{h(label)}</span>'


def _job_ai_text(job: Job) -> str:
    risks = ", ".join(RISK_UZ.get(r, r) for r in job.risk_flags or [])
    return "\n".join(x for x in [job.summary_uz or "", f"Risklar: {risks}" if risks else ""] if x)


def _posted_cell(job: Job, now: datetime) -> str:
    when = job.posted_at or job.created_at
    return f'<span title="{h(fmt_local(when))}">{h(ago_uz(when, now))}</span>'


JOB_COLS: list[Col] = [
    Col("title", "Tavsif", lambda j: j.title.lower(), lambda c, j, n: _t(j.title, 60), lambda j: j.title,
        "asc", 55, "wide"),
    Col("platform", "Platforma", lambda j: j.platform,
        lambda c, j, n: ext_link(j.url, views.PLATFORM_NAME.get(j.platform, j.platform)),
        lambda j: views.PLATFORM_NAME.get(j.platform, j.platform), "asc", 14),
    Col("budget", "Byudjet", _job_usd, lambda c, j, n: h(views._budget(j)), lambda j: views._budget(j),
        "desc", 18, "num"),
    Col("fit", "AI fit", lambda j: j.fit, lambda c, j, n: _fit_pill(j), lambda j: j.fit, "desc", 6),
    Col("win", "Yutish %", lambda j: j.win_probability,
        lambda c, j, n: h(j.win_probability) if j.win_probability is not None else "—",
        lambda j: j.win_probability, "desc", 9, "num"),
    Col("ai", "AI xulosa", None, lambda c, j, n: _t(_job_ai_text(j), 60), _job_ai_text, width=70, cls="wide"),
    Col("proposals", "Takliflar", lambda j: j.proposals_count,
        lambda c, j, n: h(j.proposals_count) if j.proposals_count is not None else "—",
        lambda j: j.proposals_count, "asc", 9, "num"),
    Col("posted", "E'lon", lambda j: as_utc(j.posted_at or j.created_at), lambda c, j, n: _posted_cell(j, n),
        lambda j: fmt_local(j.posted_at or j.created_at), "desc", 18),
    Col("status", "Holat", lambda j: str(j.status), lambda c, j, n: job_status_pill(j),
        lambda j: STATUS_LABEL_UZ.get(j.status, str(j.status)), "asc", 18),
    Col("updated", "O'zgartirilgan sana", lambda j: as_utc(j.updated_at) if j.updated_at else None,
        lambda c, j, n: _dt(j.updated_at), lambda j: fmt_local(j.updated_at), "desc", 18, "dim"),
    Col("url", "Havola", None, lambda c, j, n: "", lambda j: j.url, width=40, cls="hidden"),
]


# ── Rendering ───────────────────────────────────────────────────────────────
def table_html(ctx: Ctx, path: str, rows: list, cols: list[Col], *, sort: str, direction: str, query: dict,
               detail: Callable[[Any], str], bulk: list[tuple[str, str, str]], noun: str) -> str:
    """CRM table: checkbox column + bulk action bar, sortable headers (⇕), one-line rows that open `detail`."""
    now = now_utc()
    head = ['<th class="cb"><input type="checkbox" data-all title="Hammasini belgilash"></th>']
    for c in cols:
        if c.cls == "hidden":
            continue
        if c.sort is None:
            head.append(f'<th class="{c.cls}">{h(c.label)}</th>')
            continue
        is_cur = c.key == sort
        new_dir = ("asc" if direction == "desc" else "desc") if is_cur else c.first_dir
        icon = (SORT_UP if direction == "asc" else SORT_DOWN) if is_cur else SORT_ICON
        href = ctx.url(path, **{**query, "sort": c.key, "dir": new_dir})
        head.append(f'<th class="{c.cls}{" on" if is_cur else ""}"><a href="{h(href)}" '
                    f'title="Saralash">{h(c.label)}{icon}</a></th>')
    body = []
    for r in rows:
        tds = "".join(f'<td class="{c.cls}">{c.cell(ctx, r, now)}</td>' for c in cols if c.cls != "hidden")
        body.append(f'<tr data-href="{h(detail(r))}"><td class="cb">'
                    f'<input type="checkbox" name="ids" value="{r.id}" form="bulk"></td>{tds}</tr>')
    count = f'<p class="count">{len(rows)} ta {h(noun)}</p>'
    if not body:
        return count + '<div class="empty">Hech narsa topilmadi.</div>'
    buttons = "".join(f'<button class="{cls}" type="submit" name="action" value="{h(value)}">{h(label)}</button>'
                      for value, label, cls in bulk)
    bar = (f'<form id="bulk" class="bulk" method="post" action="{h(ctx.url(path + "/bulk"))}">'
           f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}"><input type="hidden" name="back" '
           f'value="{h(ctx.here)}"><b data-count>0</b><span class="muted">ta tanlandi</span>{buttons}</form>')
    return (f'{count}{bar}<div class="tablewrap"><table class="grid"><thead><tr>{"".join(head)}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def excel_rows(rows: list, cols: list[Col]) -> tuple[list[str], list[list[Any]], list[int]]:
    use = [c for c in cols if c.excel is not None]
    return [c.label for c in use], [[c.excel(r) for c in use] for r in rows], [c.width for c in use]
