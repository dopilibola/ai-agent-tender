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


def _win(lot: Lot) -> int | None:
    w = _a(lot).get("win_chance_percent") or []
    return w[0] if len(w) == 2 else None


def win_bar(value: int | None, text: str = "") -> str:
    """'40–60%' with a thin progress bar (value = the number the column sorts/filters by)."""
    if value is None:
        return '<span class="muted">—</span>'
    pct = max(0, min(100, int(value)))
    return f'<span class="bar">{h(text or value)}%<i><b style="width:{pct}%"></b></i></span>'


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


def pledge_uzs(lot: Lot) -> float:
    """Zakalat (bid security, blocked on the UZEX account while bidding) in so'm; 0 = not required."""
    if not lot.pledge_percent or not lot.pledge_amount:
        return 0.0
    if lot.currency == "UZS":
        return lot.pledge_amount
    return lot.pledge_amount * lot.usd_rate if lot.currency == "USD" and lot.usd_rate else 0.0


def lot_uzs(lot: Lot) -> float | None:
    """Start cost in so'm (USD lots at the lot's own rate)."""
    if lot.start_cost is None:
        return None
    if lot.currency == "UZS":
        return lot.start_cost
    return lot.start_cost * lot.usd_rate if lot.currency == "USD" and lot.usd_rate else None


def _pledge_cell(lot: Lot) -> str:
    if not lot.pledge_percent:
        return '<span class="muted">yo\'q</span>'
    v = pledge_uzs(lot)
    amount = tc.money_uzs(v) if v else tc.money_uzs(lot.pledge_amount, lot.currency)
    return f'<span>{h(amount)} <span class="muted">· {lot.pledge_percent:g}%</span></span>'


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
    Col("pledge", "Kiritiladigan pul (zakalat)", lambda lot: pledge_uzs(lot),
        lambda c, lot, n: _pledge_cell(lot), lambda lot: round(pledge_uzs(lot)), "desc", 18, "num"),
    Col("advance", "Avans", lambda lot: lot.advance_percent, lambda c, lot, n: _adv_cell(lot.advance_percent),
        lambda lot: lot.advance_percent if lot.advance_percent is not None else "", "desc", 9, "num"),
    Col("verdict", "AI xulosa", lambda lot: VERDICT_RANK.get(lot.verdict or ""),
        lambda c, lot, n: _verdict_pill(lot), lambda lot: lot.verdict or "", "desc", 14),
    Col("fit", "Fit", lambda lot: _a(lot).get("fit_score"),
        lambda c, lot, n: h(_a(lot).get("fit_score")) if _a(lot).get("fit_score") is not None else "—",
        lambda lot: _a(lot).get("fit_score"), "desc", 6, "num"),
    Col("win", "Yutish %", _win,
        lambda c, lot, n: win_bar(_win(lot), "–".join(map(str, _a(lot).get("win_chance_percent") or []))),
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
        lambda c, j, n: win_bar(j.win_probability),
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


# ── Orders (tenders + freelance in one list) ────────────────────────────────
@dataclass
class Order:
    kind: str  # tender | freelance
    id: int
    title: str
    source: str
    who: str
    usd: float | None
    price: str
    win: int | None
    win_text: str
    fit: int | None
    verdict: str  # good | maybe | bad | ""
    when: datetime | None
    status_html: str
    status_text: str
    url: str | None
    detail: str
    ai: str


def order_from_lot(ctx: Ctx, lot: Lot, now: datetime) -> Order:
    a = _a(lot)
    return Order(
        "tender", lot.id, a.get("summary_uz") or lot.title, f"{tc.SOURCE_NAME.get(lot.source, lot.source)} № "
        f"{lot.external_id}", lot.customer_name or "", _usd_cost(lot), tc.money_uzs(lot.start_cost, lot.currency),
        _win(lot), "–".join(map(str, a.get("win_chance_percent") or [])), a.get("fit_score"),
        {"QATNASHAMIZ": "good", "SHARTLI": "maybe", "YO'Q": "bad"}.get(lot.verdict or "", ""),
        as_utc(lot.deadline_at) if lot.deadline_at else None, lot_status_pill(lot, now),
        str(tc.STATUS_UZ.get(lot.status, lot.status)).split(" ", 1)[-1], lot.url, ctx.url(f"/lot/{lot.id}"), _lot_reason_text(lot))


def order_from_job(ctx: Ctx, job: Job, now: datetime) -> Order:
    fit = job.fit
    return Order(
        "freelance", job.id, job.title, views.PLATFORM_NAME.get(job.platform, job.platform),
        job.client_country or "", _job_usd(job), views._budget(job), job.win_probability, "", fit,
        "" if fit is None else "good" if fit >= 80 else "maybe" if fit >= 60 else "bad",
        as_utc(job.posted_at or job.created_at), job_status_pill(job), STATUS_LABEL_UZ.get(job.status, str(job.status)),
        job.url, ctx.url(f"/job/{job.id}"), _job_ai_text(job))


def _kind_cell(o: Order) -> str:
    return '<span class="tag">Tender</span>' if o.kind == "tender" else '<span class="tag">Frilans</span>'


def _when_cell(o: Order, now: datetime) -> str:
    if o.when is None:
        return '<span class="muted">—</span>'
    if o.kind == "freelance":
        return f'<span class="muted" title="{h(fmt_local(o.when))}">{h(ago_uz(o.when, now))}</span>'
    left = (o.when - now).total_seconds()
    cls = "urgent" if left < 86400 else "soon" if left < 3 * 86400 else ""
    return f'<span class="{cls}" title="{h(remaining_uz(o.when, now))}">{h(fmt_local(o.when))}</span>'


def _verdict_cell(o: Order) -> str:
    label = {"good": "Tavsiya", "maybe": "Shartli", "bad": "Yo'q"}.get(o.verdict)
    return f'<span class="pill {o.verdict}">{label}</span>' if label else '<span class="muted">—</span>'


ORDER_COLS: list[Col] = [
    Col("kind", "Turi", lambda o: o.kind, lambda c, o, n: _kind_cell(o),
        lambda o: "Tender" if o.kind == "tender" else "Frilans", "asc", 9),
    Col("title", "Zakaz", lambda o: o.title.lower(), lambda c, o, n: _t(o.title, 50), lambda o: o.title,
        "asc", 60, "wide"),
    Col("source", "Manba", lambda o: o.source.lower(), lambda c, o, n: ext_link(o.url, o.source),
        lambda o: o.source, "asc", 18),
    Col("who", "Buyurtmachi", lambda o: o.who.lower() or None, lambda c, o, n: _t(o.who, 26), lambda o: o.who,
        "asc", 32, "mid"),
    Col("price", "Summa", lambda o: o.usd,
        lambda c, o, n: f'<span title="{h(o.price)}">{h(f"${o.usd:,.0f}" if o.usd is not None else o.price)}</span>',
        lambda o: round(o.usd) if o.usd is not None else o.price, "desc", 14, "num"),
    Col("win", "Yutish %", lambda o: o.win, lambda c, o, n: win_bar(o.win, o.win_text), lambda o: o.win,
        "desc", 9, "num"),
    Col("fit", "Fit", lambda o: o.fit, lambda c, o, n: h(o.fit) if o.fit is not None else "—", lambda o: o.fit,
        "desc", 6, "num"),
    Col("verdict", "AI xulosa", lambda o: {"good": 3, "maybe": 2, "bad": 1}.get(o.verdict),
        lambda c, o, n: _verdict_cell(o), lambda o: o.verdict, "desc", 10),
    Col("when", "Muddat / e'lon", lambda o: o.when, lambda c, o, n: _when_cell(o, n),
        lambda o: fmt_local(o.when) if o.when else "", "asc", 18),
    Col("status", "Holat", lambda o: o.status_text, lambda c, o, n: o.status_html, lambda o: o.status_text, "asc", 16),
    Col("ai", "AI izohi", None, lambda c, o, n: _t(o.ai, 50), lambda o: o.ai, width=70, cls="wide"),
    Col("url", "Havola", None, lambda c, o, n: "", lambda o: o.url, width=40, cls="hidden"),
]


def apply_num_filters(rows: list, nf: dict, usd: Callable[[Any], Any], win: Callable[[Any], Any],
                      fit: Callable[[Any], Any]) -> list:
    """Summa ($ dan/gacha), yutish % ≥, fit ≥. A row without the value is dropped only if that filter is set."""
    def ok(v, lo=None, hi=None) -> bool:
        if lo is None and hi is None:
            return True
        if v is None:
            return False
        return (lo is None or v >= lo) and (hi is None or v <= hi)
    return [r for r in rows if ok(usd(r), nf.get("min"), nf.get("max")) and ok(win(r), nf.get("win"))
            and ok(fit(r), nf.get("fit"))]


# ── «Barcha tenderlar» (catalog: goods / services, every open lot) ─────────
KIND_PILL = {"goods": ("good", "Tovar"), "service": ("", "Xizmat / ish"), "it": ("blue", "IT")}


def cat_usd(r) -> float | None:
    return r.usd  # filled by server.py from today's CBU rate


def _cat_price(r) -> str:
    u = cat_usd(r)
    return f'<span title="{h(tc.fmt_usd(u))}">{h(tc.money_uzs(r.start_cost, r.currency))}</span>'


def _cat_title(r) -> str:
    return f"{r.title} (kategoriya)" if r.is_category else r.title


def _cat_deadline(_ctx: Ctx, r, now: datetime) -> str:
    if not r.deadline_at:
        return '<span class="muted">—</span>'
    left = (as_utc(r.deadline_at) - now).total_seconds()  # type: ignore[operator]
    cls = "urgent" if left < 86400 else "soon" if left < 3 * 86400 else ""
    return (f'<span class="{cls}" title="{h(remaining_uz(r.deadline_at, now))}">'
            f'{h(fmt_local(r.deadline_at))}</span>')


def _cat_direction(r) -> str:
    from app.tenders.catalog import DIRECTION_LABEL
    return DIRECTION_LABEL.get(r.direction, "Boshqa")


def _cat_kind(r) -> str:
    cls, label = KIND_PILL.get(r.kind, ("", r.kind))
    return f'<span class="pill {cls}">{h(label)}</span>'


GOODS_VERDICT = {"QATNASHISH MUMKIN": ("good", "Qatnashsa bo'ladi", 0), "SHARTLI": ("maybe", "Shartli", 1),
                 "TAVSIYA ETILMAYDI": ("bad", "Tavsiya etilmaydi", 2)}


def _cat_pledge_cell(r) -> str:
    if r.pledge_uzs is None:
        return ('<span class="muted" title="Lot kartasi hali o\'qilmagan">…</span>' if r.kind == "goods"
                else '<span class="muted">—</span>')
    if not r.pledge_uzs:
        return '<span class="muted">yo\'q</span>'
    return f'{h(tc.money_uzs(r.pledge_uzs))} <span class="muted">· {r.pledge_pct:g}%</span>'


def _cat_verdict_cell(r) -> str:
    if not r.ai_verdict:
        return '<span class="pill">Kutilmoqda</span>' if r.kind == "goods" else '<span class="muted">—</span>'
    cls, label, _ = GOODS_VERDICT.get(r.ai_verdict, ("", r.ai_verdict, 3))
    return f'<span class="pill {cls}">{h(label)}</span>'


def _cat_win(r) -> int | None:
    return r.win[0] if r.win and len(r.win) == 2 else None


# Money bands for the «Tovar va xizmat» chips: (key, label, from so'm, to so'm). Several can be chosen.
SUM_BANDS = (("s1", "50 mln gacha", 0, 50e6), ("s2", "50–200 mln", 50e6, 200e6), ("s3", "200 mln – 1 mlrd", 200e6, 1e9),
             ("s4", "1–5 mlrd", 1e9, 5e9), ("s5", "5 mlrd dan ko'p", 5e9, float("inf")))
PLEDGE_BANDS = (("z0", "Zakalatsiz", 0, 0), ("z1", "5 mln gacha", 0, 5e6), ("z2", "5–20 mln", 5e6, 20e6),
                ("z3", "20–100 mln", 20e6, 100e6), ("z4", "100 mln dan ko'p", 100e6, float("inf")))
ADVANCE_BANDS = (("a0", "Avanssiz", 0, 0), ("a1", "20% gacha", 0, 20), ("a2", "20–30%", 20, 30),
                 ("a3", "30–50%", 30, 50), ("a4", "50% dan ko'p", 50, float("inf")))
BAND_GROUPS = {"sm": SUM_BANDS, "zk": PLEDGE_BANDS, "av": ADVANCE_BANDS}
BAND_TITLES = {"sm": "Boshlang'ich summa", "zk": "Zakalat", "av": "Avans"}


def _adv_cell(v: float | None) -> str:
    if v is None:
        return '<span class="muted">—</span>'
    return f"{v:g}%" if v else '<span class="muted">yo\'q</span>'


CATALOG_COLS: list[Col] = [
    Col("title", "Nomi", lambda r: r.title.lower(), lambda c, r, n: _t(_cat_title(r), 70), _cat_title,
        "asc", 70, "wide"),
    Col("kind", "Turi", lambda r: r.kind, lambda c, r, n: _cat_kind(r),
        lambda r: KIND_PILL.get(r.kind, ("", r.kind))[1], "asc", 12, "hidden"),
    Col("dir", "Yo'nalish", lambda r: r.direction or None,
        lambda c, r, n: _t(_cat_direction(r), 26), _cat_direction, "asc", 26, "mid"),
    Col("no", "Lot №", lambda r: int(r.external_id) if r.external_id.isdigit() else None,
        lambda c, r, n: f'<span class="lotno">{ext_link(r.url, r.external_id)}</span>',
        lambda r: r.external_id, "desc", 14),
    Col("source", "Manba", lambda r: f"{r.source} {r.lot_type or ''}".lower(),
        lambda c, r, n: _t(f"{tc.SOURCE_NAME.get(r.source, r.source)} · {r.lot_type or '?'}", 30),
        lambda r: f"{tc.SOURCE_NAME.get(r.source, r.source)} · {r.lot_type or ''}", "asc", 24, "hidden"),
    Col("deadline", "Muddat", lambda r: as_utc(r.deadline_at) if r.deadline_at else None, _cat_deadline,
        lambda r: fmt_local(r.deadline_at) if r.deadline_at else "", "asc", 18),
    Col("price", "Narx", lambda r: cat_usd(r) if cat_usd(r) is not None else r.start_cost,
        lambda c, r, n: _cat_price(r), lambda r: r.start_cost, "desc", 16, "num"),
    Col("pledge", "Kiritiladigan pul (zakalat)", lambda r: r.pledge_uzs, lambda c, r, n: _cat_pledge_cell(r),
        lambda r: round(r.pledge_uzs) if r.pledge_uzs is not None else "", "desc", 18, "num"),
    Col("advance", "Avans", lambda r: r.advance_pct, lambda c, r, n: _adv_cell(r.advance_pct),
        lambda r: r.advance_pct if r.advance_pct is not None else "", "desc", 9, "num"),
    Col("verdict", "AI xulosa", lambda r: GOODS_VERDICT[r.ai_verdict][2] if r.ai_verdict in GOODS_VERDICT else None,
        lambda c, r, n: _cat_verdict_cell(r), lambda r: GOODS_VERDICT.get(r.ai_verdict, ("", r.ai_verdict))[1],
        "asc", 16),
    Col("win", "Yutish %", _cat_win, lambda c, r, n: win_bar(_cat_win(r), "–".join(map(str, r.win or []))),
        lambda r: "–".join(map(str, r.win or [])), "desc", 9, "num"),
    Col("customer", "Buyurtmachi", lambda r: (r.customer_name or "").lower() or None,
        lambda c, r, n: _t(r.customer_name, 36), lambda r: r.customer_name or "", "asc", 40, "mid"),
    Col("region", "Hudud", lambda r: (r.customer_region or "").lower() or None,
        lambda c, r, n: _t(r.customer_region, 24), lambda r: r.customer_region or "", "asc", 24, "mid"),
    Col("url", "Havola", None, lambda c, r, n: "", lambda r: r.url, width=40, cls="hidden"),
]


# ── Rendering ───────────────────────────────────────────────────────────────
def table_html(ctx: Ctx, path: str, rows: list, cols: list[Col], *, sort: str, direction: str, query: dict,
               detail: Callable[[Any], str], bulk: list[tuple[str, str, str]], noun: str,
               external: bool | Callable[[Any], bool] = False, foot: dict[str, str] | None = None) -> str:
    """CRM table: optional checkbox column + bulk bar, sortable headers, one-line rows that open `detail`."""
    now = now_utc()
    head = ['<th class="cb"><input type="checkbox" data-all title="Hammasini belgilash"></th>'] if bulk else []
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
        cb = (f'<td class="cb"><input type="checkbox" name="ids" value="{r.id}" form="bulk"></td>' if bulk else "")
        ext = ' data-ext="1"' if (external(r) if callable(external) else external) else ""
        body.append(f'<tr data-href="{h(detail(r))}"{ext}>{cb}{tds}</tr>')
    count = f'<p class="count">{len(rows)} ta {h(noun)}</p>'
    if not body:
        return count + '<div class="empty">Hech narsa topilmadi. Filtrlarni yumshatib ko\'ring.</div>'
    bar = ""
    if bulk:
        buttons = "".join(f'<button class="{cls}" type="submit" name="action" value="{h(value)}">{h(label)}</button>'
                          for value, label, cls in bulk)
        bar = (f'<form id="bulk" class="bulk" method="post" action="{h(ctx.url(path.rstrip("/") + "/bulk"))}">'
               f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}"><input type="hidden" name="back" '
               f'value="{h(ctx.here)}"><b data-count>0</b><span class="muted">ta tanlandi</span>{buttons}</form>')
    tfoot = ""
    if foot:  # one totals row: cell html by column key
        cells = ('<td class="cb"></td>' if bulk else "") + "".join(
            f'<td class="{c.cls}">{foot.get(c.key, "")}</td>' for c in cols if c.cls != "hidden")
        tfoot = f"<tfoot><tr>{cells}</tr></tfoot>"
    return (f'{count}{bar}<div class="tablewrap"><table class="grid"><thead><tr>{"".join(head)}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody>{tfoot}</table></div>')


def excel_rows(rows: list, cols: list[Col]) -> tuple[list[str], list[list[Any]], list[int]]:
    use = [c for c in cols if c.excel is not None]
    return [c.label for c in use], [[c.excel(r) for c in use] for r in rows], [c.width for c in use]


# ── «Uzum narxlari»: tender unit price vs Uzum Market ─────────────────────
def _qty(r) -> str:
    q = r.item.qty
    if q is None:
        return r.item.unit or ""
    return f"{q:,.0f}".replace(",", " ") + (f" {r.item.unit}" if r.item.unit else "")


def _ratio_text(r) -> str:
    if not r.ratio:
        return "—"
    if r.ratio >= 1:
        return f"×{r.ratio:.1f} qimmat"
    return f"×{1 / r.ratio:.1f} arzon" if r.ratio else "—"


def _status_cell(r) -> str:
    cls = {"ok": "good", "pending": "blue", "nomatch": "", "far": "maybe"}.get(r.status, "")
    return f'<span class="pill {cls}" title="{h(r.status_text)}">{h(tc.short(r.status_text, 34))}</span>'


def _ratio_cell(r, big: float) -> str:
    cls = "bad" if r.ratio >= big else "good" if r.ratio and r.ratio <= 1 / big else "maybe" if r.ratio >= 1.3 else ""
    return f'<span class="pill {cls}">{h(_ratio_text(r))}</span>'


def _uzum_cell(r) -> str:
    if r.status == "ai":
        return (f'<span title="{h("AI bozor narxi taxmini: " + r.note)}">🤖 {h(tc.money_uzs(r.uzum_med))}</span>')
    if not r.uzum_med:
        return f'<span class="muted">{ext_link(r.uzum_url, "qidirish")}</span>' if r.status != "unit" else "—"
    tip = (f"{r.n} ta mos mahsulot · eng arzoni {tc.money_uzs(r.uzum_min)}"
           + (f" · masalan: {r.best.title}" if r.best else ""))
    return f'<span title="{h(tip)}">{ext_link(r.uzum_url, tc.money_uzs(r.uzum_med))}</span>'


def _go_cell(r) -> str:
    """Two buttons: the lot on the tender site and the item's search on Uzum."""
    return (ext_link(r.lot.url, "📄 Tender", "btn ghost xs")
            + (" " + ext_link(r.uzum_url, "🛒 Uzum", "btn ghost xs") if r.status != "unit" else ""))


def price_cols(big: float) -> list[Col]:
    return [
        Col("name", "Mahsulot (tenderda)", lambda r: r.item.name.lower(), lambda c, r, n: _t(r.item.name, 60),
            lambda r: r.item.name, "asc", 60, "wide"),
        Col("no", "Lot №", lambda r: int(r.lot.external_id) if r.lot.external_id.isdigit() else None,
            lambda c, r, n: f'<span class="lotno">{ext_link(r.lot.url, r.lot.external_id)}</span>',
            lambda r: r.lot.external_id, "desc", 12),
        Col("qty", "Miqdori", lambda r: r.item.qty, lambda c, r, n: h(_qty(r)), _qty, "desc", 14, "num"),
        Col("tender", "Tender narxi (1 ta)", lambda r: r.price_uzs or None,
            lambda c, r, n: h(tc.money_uzs(r.price_uzs)),
            lambda r: round(r.price_uzs), "desc", 18, "num"),
        Col("uzum", "Uzum narxi (o'rtacha)", lambda r: r.uzum_med or None, lambda c, r, n: _uzum_cell(r),
            lambda r: round(r.uzum_med) if r.uzum_med else "", "desc", 18, "num"),
        Col("ratio", "Farq", lambda r: r.ratio or None, lambda c, r, n: _ratio_cell(r, big), _ratio_text, "desc", 14),
        Col("over", "Farq summasi", lambda r: r.overpay if r.ratio else None,
            lambda c, r, n: ('<span class="muted">—</span>' if not r.ratio else h(tc.money_uzs(r.overpay))
                             if r.overpay >= 0 else f'<span class="muted">−{h(tc.money_uzs(-r.overpay))}</span>'),
            lambda r: round(r.overpay) if r.ratio else "", "desc", 18, "num"),
        *gap_lot_cols(),
        Col("status", "Holat", lambda r: r.status, lambda c, r, n: _status_cell(r), lambda r: r.status_text,
            "asc", 30),
        Col("deadline", "Muddat", lambda r: as_utc(r.lot.deadline_at) if r.lot.deadline_at else None,
            lambda c, r, n: _cat_deadline(c, r.lot, n),
            lambda r: fmt_local(r.lot.deadline_at) if r.lot.deadline_at else "", "asc", 18),
        Col("customer", "Buyurtmachi", lambda r: (r.lot.customer_name or "").lower() or None,
            lambda c, r, n: _t(r.lot.customer_name, 30), lambda r: r.lot.customer_name or "", "asc", 36, "mid"),
        Col("go", "Ochish", None, lambda c, r, n: _go_cell(r), width=20),
        Col("match", "Uzum'dagi o'xshash", None, lambda c, r, n: "",
            lambda r: r.best.title if r.best else "", width=50, cls="hidden"),
        Col("uzum_url", "Uzum havola", None, lambda c, r, n: "", lambda r: r.uzum_url, width=40, cls="hidden"),
        Col("url", "Lot havola", None, lambda c, r, n: "", lambda r: r.lot.url, width=40, cls="hidden"),
    ]


def gap_lot_cols() -> list[Col]:
    """Lot-level columns for item rows (r.lot is a CatalogRow with pledge / AI fields filled in)."""
    return [
        Col("lprice", "Boshlang'ich narx", lambda r: cat_usd(r.lot) if cat_usd(r.lot) is not None else r.lot.start_cost,
            lambda c, r, n: _cat_price(r.lot), lambda r: r.lot.start_cost, "desc", 16, "num"),
        Col("pledge", "Kiritiladigan pul (zakalat)", lambda r: getattr(r.lot, "pledge_uzs", None),
            lambda c, r, n: _cat_pledge_cell(r.lot), None, "desc", 18, "num"),
        Col("verdict", "AI xulosa", lambda r: GOODS_VERDICT[r.lot.ai_verdict][2]
            if getattr(r.lot, "ai_verdict", "") in GOODS_VERDICT else None,
            lambda c, r, n: _cat_verdict_cell(r.lot), None, "asc", 16),
        Col("win", "Yutish %", lambda r: _cat_win(r.lot),
            lambda c, r, n: win_bar(_cat_win(r.lot), "–".join(map(str, r.lot.win or []))), None, "desc", 9, "num"),
    ]


def lot_price_cols(big: float) -> list[Col]:
    """«Lot bo'yicha»: one row per lot — how many items, how many compared, how much dearer than Uzum."""
    return [
        Col("no", "Lot №", lambda r: int(r.lot.external_id) if r.lot.external_id.isdigit() else None,
            lambda c, r, n: f'<span class="lotno">{ext_link(r.lot.url, r.lot.external_id)}</span>',
            lambda r: r.lot.external_id, "desc", 12),
        Col("title", "Lot nomi", lambda r: r.lot.title.lower(), lambda c, r, n: _t(r.lot.title, 60),
            lambda r: r.lot.title, "asc", 60, "wide"),
        Col("items", "Mahsulotlar", lambda r: len(r.rows), lambda c, r, n: str(len(r.rows)),
            lambda r: len(r.rows), "desc", 12, "num"),
        Col("checked", "Solishtirildi", lambda r: len(r.compared),
            lambda c, r, n: f"{len(r.compared)} / {len(r.rows)}", lambda r: len(r.compared), "desc", 14, "num"),
        Col("dearer", "Uzum arzon", lambda r: len(r.dearer(big)),
            lambda c, r, n: (f'<span class="pill bad">{len(r.dearer(big))} ta</span>' if r.dearer(big)
                             else '<span class="muted">—</span>'), lambda r: len(r.dearer(big)), "desc", 12),
        Col("over", "Ortiqcha summa", lambda r: r.overpay(big) or None,
            lambda c, r, n: h(tc.money_uzs(r.overpay(big))) if r.overpay(big) else '<span class="muted">—</span>',
            lambda r: round(r.overpay(big)), "desc", 18, "num"),
        *gap_lot_cols(),
        Col("deadline", "Muddat", lambda r: as_utc(r.lot.deadline_at) if r.lot.deadline_at else None,
            lambda c, r, n: _cat_deadline(c, r.lot, n),
            lambda r: fmt_local(r.lot.deadline_at) if r.lot.deadline_at else "", "asc", 18),
        Col("customer", "Buyurtmachi", lambda r: (r.lot.customer_name or "").lower() or None,
            lambda c, r, n: _t(r.lot.customer_name, 30), lambda r: r.lot.customer_name or "", "asc", 36, "mid"),
        Col("go", "Ochish", None, lambda c, r, n: ext_link(r.lot.url, "📄 Tender", "btn ghost xs"), width=20),
        Col("url", "Lot havola", None, lambda c, r, n: "", lambda r: r.lot.url, width=40, cls="hidden"),
    ]
