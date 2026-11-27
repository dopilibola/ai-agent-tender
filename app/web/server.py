"""Private dashboard (aiohttp, runs inside the bot process and shares its DB/services).

Every page needs a login (WEB_ADMIN_USER / WEB_ADMIN_PASSWORD from .env). After login the site shows the same
messages the bot sends to Telegram, plus one orders table (tenders + freelance) with filters and the
separate tender / freelance lists and their buttons.
"""

from __future__ import annotations

from collections.abc import Callable

import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import defer

from app.bot.feed import KINDS
from app.config import Settings
from app.core.states import JobStatus
from app.db.models import Job, Lot, LotStatus, SentMessage
from app.pipeline.service import FreelanceService
from app.tenders import cards as tc
from app.tenders.catalog import DIRECTION_LABEL
from app.tenders.filters import is_it_lot
from app.tenders.prices import LotPrices, group_by_lot
from app.tenders.service import ACTIVE, TenderService
from app.tenders.timeutil import TASHKENT, as_utc, now_utc
from app.web import tables, views, xlsx
from app.web.auth import COOKIE, SESSION_TTL_S, Auth

FLASH = "fh_flash"
THEME = "fh_theme"

log = logging.getLogger(__name__)

S = JobStatus
HIDDEN_JOB = (S.SKIPPED, S.FOUND, S.EXPIRED)
VERDICT_KEY = {"good": "QATNASHAMIZ", "maybe": "SHARTLI", "bad": "YO'Q"}
LOT_ACTIONS = {"bid": (LotStatus.BIDDING, "📌 Qatnashamiz"), "unbid": (LotStatus.NOTIFIED, "↩️ Qatnashmaymiz"),
               "ignore": (LotStatus.IGNORED, "🚫 Kerak emas")}
MAX_ROWS = 300
# List pages never read the platform's raw JSON (~70 KB per lot), so it is not loaded for them.
LOT_LIST = (defer(Lot.raw, raiseload=True),)
JOB_LIST = (defer(Job.raw, raiseload=True),)


@dataclass
class Deps:
    settings: Settings
    sessionmaker: async_sessionmaker
    tenders: TenderService | None
    freelance: FreelanceService | None
    notify: Callable[[str], Awaitable[None]] | None
    auth: Auth
    base: str
    title: str


DEPS = web.AppKey("deps", Deps)


def _ip(request: web.Request) -> str:
    # Cloudflare tunnel → nginx → us: the first two headers carry the real client.
    return (request.headers.get("CF-Connecting-IP") or request.headers.get("X-Real-IP")
            or request.remote or "?").strip()


def _ctx(request: web.Request) -> views.Ctx:
    d = request.app[DEPS]
    exp = d.auth.session_exp(request.cookies.get(COOKIE))
    flash = ""
    if raw := request.cookies.get(FLASH):
        with contextlib.suppress(ValueError):
            flash = bytes.fromhex(raw).decode("utf-8", "ignore")
    return views.Ctx(d.base, d.title, exp, d.auth.csrf(exp) if exp else None, d.auth.enabled,
                     here=request.path_qs, flash=flash, theme=request.cookies.get(THEME, "auto"))


def _html(text: str, status: int = 200) -> web.Response:
    return web.Response(text=text, status=status, content_type="text/html", charset="utf-8")


def _not_found(request: web.Request, what: str) -> web.Response:
    ctx = _ctx(request)
    return _html(views.layout(ctx, "", f'<div class="empty">{views.h(what)} topilmadi.</div>', title="Topilmadi"), 404)


def _flash_redirect(location: str, msg: str) -> web.HTTPSeeOther:
    resp = web.HTTPSeeOther(location)
    resp.set_cookie(FLASH, msg[:200].encode().hex(), max_age=30, httponly=True, samesite="Strict")
    return resp


def _page(request: web.Request, ctx: views.Ctx, html: str) -> web.Response:
    resp = _html(html)
    if FLASH in request.cookies:
        resp.del_cookie(FLASH)
    return resp


OPEN_PATHS = ("/login", "/healthz", "/static/app.js")
APP_JS = (Path(__file__).parent / "static" / "app.js").read_bytes()
views.ASSET_VER = hashlib.sha256(APP_JS).hexdigest()[:10]


@web.middleware
async def require_login(request: web.Request, handler):
    d = request.app[DEPS]
    rel = request.path[len(d.base):] if d.base and request.path.startswith(d.base) else request.path
    if rel not in OPEN_PATHS and rel != "" and d.auth.session_exp(request.cookies.get(COOKIE)) is None:
        target = f"{d.base}/login"
        if request.method == "GET" and rel not in ("/", ""):
            target += "?" + urlencode({"next": request.path_qs})
        raise web.HTTPSeeOther(target)
    return await handler(request)


@web.middleware
async def security_headers(request: web.Request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    )
    resp.headers.setdefault("Cache-Control", "private, no-store")
    resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return resp


# ── Telegram feed ───────────────────────────────────────────────────────────
FEED_PAGE = 50


async def feed_index(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    ctx = _ctx(request)
    kinds = list(KINDS)
    kind = request.query.get("k", "")
    kind = kind if kind in kinds else ""
    before = request.query.get("before", "")
    async with d.sessionmaker() as s:
        stmt = select(SentMessage).where(SentMessage.kind.in_([kind] if kind else kinds))
        if before.isdigit():
            stmt = stmt.where(SentMessage.id < int(before))
        rows = list((await s.scalars(stmt.order_by(SentMessage.id.desc()).limit(FEED_PAGE + 1))).all())
    next_before = rows[FEED_PAGE - 1].id if len(rows) > FEED_PAGE else None
    return _page(request, ctx, views.feed_page(ctx, rows[:FEED_PAGE], kind=kind, kinds=kinds,
                                               next_before=next_before))


# ── Numeric filters (summa / yutish % / fit) ─────────────────────────────────
def _num_filters(request: web.Request) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key in ("min", "max", "win", "fit"):
        raw = request.query.get(key, "").strip().replace(" ", "").replace(",", ".")
        try:
            val = float(raw) if raw else None
        except ValueError:
            val = None
        out[key] = val if val is not None and 0 <= val < 1e13 else None
    return out


# ── Tenders ─────────────────────────────────────────────────────────────────
def _sort_params(request: web.Request, cols, default: str, default_dir: str) -> tuple[str, str]:
    sort = request.query.get("sort", default)
    if not any(c.key == sort and c.sort for c in cols):
        sort = default
    direction = request.query.get("dir", default_dir)
    return sort, direction if direction in ("asc", "desc") else default_dir


async def _query_lots(request: web.Request) -> tuple[list[Lot], dict]:
    d = request.app[DEPS]
    q = request.query.get("q", "").strip()[:100]
    verdict = request.query.get("v", "")
    status = request.query.get("s", "open")
    sort, direction = _sort_params(request, tables.LOT_COLS, "deadline", "asc")
    nf = _num_filters(request)
    now = now_utc()
    async with d.sessionmaker() as s:
        stmt = select(Lot).options(*LOT_LIST)
        if status == "bidding":
            stmt = stmt.where(Lot.status == LotStatus.BIDDING)
        elif status != "all":
            stmt = stmt.where(Lot.status.in_(ACTIVE))
        if verdict in VERDICT_KEY:
            stmt = stmt.where(Lot.verdict == VERDICT_KEY[verdict])
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Lot.title.ilike(like), Lot.customer_name.ilike(like), Lot.external_id.ilike(like),
                                  Lot.description.ilike(like)))
        order = Lot.deadline_at.asc() if status != "all" else Lot.id.desc()
        lots = list((await s.scalars(stmt.order_by(order).limit(MAX_ROWS))).all())
        open_all = (await s.scalars(select(Lot).options(*LOT_LIST).where(Lot.status.in_(ACTIVE)))).all()
    if status != "all":  # "all" = everything, incl. closed and lots the AI said are not IT services
        lots = [lot for lot in lots if (not lot.deadline_at or as_utc(lot.deadline_at) > now) and is_it_lot(lot)]
    lots = tables.apply_num_filters(lots, nf, tables._usd_cost, tables._win, lambda lot: tables._a(lot).get("fit_score"))
    lots, bands, _ = _chip_filters(request, lots, {"sm": tables.lot_uzs, "zk": tables.pledge_uzs,
                                                   "av": lambda lot: lot.advance_percent})
    lots = tables.sort_rows(lots, tables.LOT_COLS, sort, direction)
    live = [lot for lot in open_all if (not lot.deadline_at or as_utc(lot.deadline_at) > now) and is_it_lot(lot)]
    counts = {
        "open": len(live),
        "good": sum(lot.verdict == "QATNASHAMIZ" for lot in live),
        "maybe": sum(lot.verdict == "SHARTLI" for lot in live),
        "bidding": sum(lot.status == LotStatus.BIDDING for lot in live),
    }
    return lots, {"q": q, "verdict": verdict, "status": status, "counts": counts, "sort": sort,
                  "direction": direction, "nf": nf, **bands}


async def tenders_index(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    lots, p = await _query_lots(request)
    health = await d.tenders.health() if d.tenders else {}
    ctx = _ctx(request)
    return _page(request, ctx, views.tenders_page(ctx, lots, health=health, **p))


def _xlsx_response(name: str, sheet: str, rows: list, cols) -> web.Response:
    header, data, widths = tables.excel_rows(rows, cols)
    stamp = now_utc().astimezone(TASHKENT).strftime("%Y-%m-%d_%H%M")
    return web.Response(
        body=xlsx.build(sheet, header, data, widths),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}_{stamp}.xlsx"'},
    )


async def tenders_xlsx(request: web.Request) -> web.Response:
    lots, _ = await _query_lots(request)
    return _xlsx_response("tenderlar", "Tenderlar", lots, tables.LOT_COLS)


async def lot_detail(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    try:
        lot_id = int(request.match_info["id"])
    except ValueError:
        return _not_found(request, "Lot")
    async with d.sessionmaker() as s:
        lot = await s.get(Lot, lot_id)
        if lot is None:
            return _not_found(request, "Lot")
        used = await d.tenders.deposit_used_usd(s, exclude_id=lot.id) if d.tenders else 0.0
    limit = d.tenders.limit_usd if d.tenders else 0.0
    ctx = _ctx(request)
    return _page(request, ctx, views.lot_page(ctx, lot, used_usd=used, limit_usd=limit))


# ── Freelance ───────────────────────────────────────────────────────────────
async def _query_jobs(request: web.Request) -> tuple[list[Job], dict]:
    d = request.app[DEPS]
    q = request.query.get("q", "").strip()[:100]
    platform = request.query.get("p", "")
    show = request.query.get("show", "good")
    sort, direction = _sort_params(request, tables.JOB_COLS, "posted", "desc")
    nf = _num_filters(request)
    async with d.sessionmaker() as s:
        stmt = select(Job).options(*JOB_LIST)
        if show != "all":
            stmt = stmt.where(Job.status.not_in(HIDDEN_JOB))
        if platform:
            stmt = stmt.where(Job.platform == platform)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Job.title.ilike(like), Job.description.ilike(like)))
        jobs = list((await s.scalars(stmt.order_by(Job.id.desc()).limit(MAX_ROWS))).all())
        platforms = sorted((await s.scalars(select(Job.platform).distinct())).all())
        by_status = dict((await s.execute(select(Job.status, func.count()).group_by(Job.status))).all())
    counts = {
        "active": sum(n for st, n in by_status.items() if st not in HIDDEN_JOB),
        "awaiting": by_status.get(S.AWAITING_APPROVAL, 0),
        "submitted": sum(by_status.get(x, 0) for x in (S.SUBMITTED, S.CLIENT_REPLIED, S.INTERVIEW)),
        "hired": sum(by_status.get(x, 0) for x in (S.HIRED, S.IN_PROGRESS, S.REVIEW_READY, S.DELIVERED, S.PAID)),
    }
    jobs = tables.apply_num_filters(jobs, nf, tables._job_usd, lambda j: j.win_probability, lambda j: j.fit)
    jobs = tables.sort_rows(jobs, tables.JOB_COLS, sort, direction)
    return jobs, {"q": q, "platform": platform, "show": show, "platforms": platforms, "counts": counts,
                  "sort": sort, "direction": direction, "nf": nf}


async def jobs_index(request: web.Request) -> web.Response:
    jobs, p = await _query_jobs(request)
    ctx = _ctx(request)
    return _page(request, ctx, views.jobs_page(ctx, jobs, **p))


async def jobs_xlsx(request: web.Request) -> web.Response:
    jobs, _ = await _query_jobs(request)
    return _xlsx_response("frilans_ishlar", "Frilans ishlar", jobs, tables.JOB_COLS)


async def job_detail(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    try:
        job_id = int(request.match_info["id"])
    except ValueError:
        return _not_found(request, "Ish")
    if d.freelance is None:
        return _not_found(request, "Ish")
    async with d.sessionmaker() as s:
        job, proposal = await d.freelance.latest(s, job_id)
    if job is None:
        return _not_found(request, "Ish")
    ctx = _ctx(request)
    return _page(request, ctx, views.job_page(ctx, job, proposal))


# ── Orders: open tenders + recommended freelance jobs in one table ──────────
async def _query_orders(request: web.Request, ctx: views.Ctx) -> tuple[list, dict]:
    d = request.app[DEPS]
    t = request.query.get("t", "")
    t = t if t in ("tender", "freelance") else ""
    q = request.query.get("q", "").strip()[:100]
    verdict = request.query.get("v", "")
    verdict = verdict if verdict in ("good", "maybe", "bad") else ""
    sort, direction = _sort_params(request, tables.ORDER_COLS, "win", "desc")
    nf = _num_filters(request)
    now = now_utc()
    rows: list[tables.Order] = []
    async with d.sessionmaker() as s:
        if t != "freelance":
            stmt = select(Lot).options(*LOT_LIST).where(Lot.status.in_(ACTIVE))
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(Lot.title.ilike(like), Lot.customer_name.ilike(like),
                                      Lot.external_id.ilike(like), Lot.description.ilike(like)))
            for lot in (await s.scalars(stmt.order_by(Lot.deadline_at.asc()).limit(MAX_ROWS))).all():
                if (not lot.deadline_at or as_utc(lot.deadline_at) > now) and is_it_lot(lot):
                    rows.append(tables.order_from_lot(ctx, lot, now))
        if t != "tender":
            stmt = select(Job).options(*JOB_LIST).where(Job.status.not_in(HIDDEN_JOB))
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(Job.title.ilike(like), Job.description.ilike(like), Job.platform.ilike(like)))
            rows += [tables.order_from_job(ctx, j, now)
                     for j in (await s.scalars(stmt.order_by(Job.id.desc()).limit(MAX_ROWS))).all()]
    if verdict:
        rows = [r for r in rows if r.verdict == verdict]
    rows = tables.apply_num_filters(rows, nf, lambda r: r.usd, lambda r: r.win, lambda r: r.fit)
    rows = tables.sort_rows(rows, tables.ORDER_COLS, sort, direction)
    return rows, {"t": t, "q": q, "verdict": verdict, "nf": nf, "sort": sort, "direction": direction}


async def orders_index(request: web.Request) -> web.Response:
    """Start page: IT tenders, goods tenders and freelance jobs as three separate cards."""
    ctx = _ctx(request)
    if request.query:  # old links with filters (?t=…) → the separate pages
        t = request.query.get("t")
        raise web.HTTPSeeOther(ctx.url("/jobs" if t == "freelance" else "/lots"))
    rows, _ = await _query_orders(request, ctx)
    try:
        goods, _ = await _query_catalog(request)
    except Exception:  # the start page must open even if the lot list is broken
        log.exception("web.home_goods_failed")
        goods = []
    pledge = sum(r.pledge_uzs or 0 for r in goods)
    return _page(request, ctx, views.home_page(ctx, it=[r for r in rows if r.kind == "tender"], goods=goods,
                                               jobs=[r for r in rows if r.kind == "freelance"], goods_pledge=pledge))


async def orders_xlsx(request: web.Request) -> web.Response:
    rows, _ = await _query_orders(request, _ctx(request))
    return _xlsx_response("zakazlar", "Zakazlar", rows, tables.ORDER_COLS)


# ── «Barcha tenderlar»: every open lot (goods / services / IT) ─────────────
CATALOG_SHOWN = 500


def _set_lot_infos(infos: dict, rows: list) -> None:
    """Zakalat / AI verdict / win % of each row's lot (r.lot is a CatalogRow), for the lot columns."""
    for r in rows:
        info = infos.get(r.lot.id) or {}
        r.lot.pledge_pct, r.lot.pledge_uzs = info.get("pledge_pct"), info.get("pledge_uzs")
        r.lot.ai_verdict, r.lot.win = info.get("ai_verdict") or "", info.get("win")
        r.lot.advance_pct = info.get("advance_pct")


async def _fill_lot_infos(pw, rows: list) -> None:
    try:
        _set_lot_infos(await pw.lot_infos(), rows)
    except Exception:  # the price table must still open if this breaks
        log.exception("web.lot_infos_failed")


def _directions(request: web.Request) -> set[str]:
    """?y=furniture,fuel (or ?y=furniture&y=fuel) → the chosen yo'nalishlar; unknown keys are dropped."""
    raw = ",".join(request.query.getall("y", []))
    return {k for k in (x.strip() for x in raw.split(",")) if k in DIRECTION_LABEL}


def _keys(request: web.Request, name: str, allowed) -> set[str]:
    raw = ",".join(request.query.getall(name, []))
    return {k for k in (x.strip() for x in raw.split(",")) if k in allowed}


def _in_band(v: float | None, band: tuple) -> bool:
    _, _, lo, hi = band
    if v is None:
        return False
    if hi == 0:  # «Zakalatsiz»
        return v == 0
    return lo < v <= hi if lo else 0 < v <= hi


def _band_of(bands, chosen: set[str]):
    picked = [b for b in bands if b[0] in chosen]
    return lambda v: any(_in_band(v, b) for b in picked)


def _chip_filters(request: web.Request, rows: list, getters: dict, extra: dict | None = None
                  ) -> tuple[list, dict, Callable]:
    """Band chips (?sm=…&zk=…&av=…, see tables.BAND_GROUPS) plus any extra tests {param: row → bool}.
    Returns the rows that pass, the chosen keys and button counts for the page, and keep(row, skip) —
    every group's counts are taken with the *other* groups applied, so a button shows what a click gives."""
    tests = dict(extra or {})
    out: dict = {}
    for param, bands in tables.BAND_GROUPS.items():
        chosen = _keys(request, param, {b[0] for b in bands})
        out[param] = ",".join(sorted(chosen))
        if chosen:
            match, get = _band_of(bands, chosen), getters[param]
            tests[param] = lambda r, m=match, g=get: m(g(r))

    def keep(r, skip: str = "") -> bool:
        return all(t(r) for k, t in tests.items() if k != skip)

    out["band_counts"] = {param: {b[0]: sum(1 for r in rows if keep(r, param) and _in_band(getters[param](r), b))
                                  for b in bands} for param, bands in tables.BAND_GROUPS.items()}
    return [r for r in rows if keep(r)], out, keep


async def _query_catalog(request: web.Request) -> tuple[list, dict]:
    d = request.app[DEPS]
    kind = request.query.get("k", "goods")
    kind = kind if kind in ("goods", "service") else "goods"  # IT lots live on «IT tenderlar» (/lots)
    src = request.query.get("src", "")
    src = src if src in tc.SOURCE_SITE else ""
    region = request.query.get("r", "").strip()[:128]
    yset = _directions(request)
    yon = ",".join(sorted(yset))
    q = request.query.get("q", "").strip()[:100]
    sort, direction = _sort_params(request, tables.CATALOG_COLS, "deadline", "asc")
    nf = {k: v for k, v in _num_filters(request).items() if k in ("min", "max")}
    now = now_utc()
    rows, updated = await d.tenders.catalog() if d.tenders else ([], {})
    rate = await d.tenders.fx.get() if d.tenders and d.tenders.fx else None
    live = [r for r in rows if not r.deadline_at or as_utc(r.deadline_at) > now]
    pw = d.tenders.prices if d.tenders else None
    try:
        infos = await pw.lot_infos() if pw else {}
    except Exception:  # the lot list must still open if the price data is broken
        log.exception("web.lot_infos_failed")
        infos = {}
    for r in live:
        r.usd = tc.usd(r.start_cost, r.currency, rate)
        r.uzs = r.start_cost if r.currency == "UZS" else (r.usd * rate if r.usd is not None and rate else None)
        info = infos.get(r.id) or {}
        r.pledge_pct, r.pledge_uzs = info.get("pledge_pct"), info.get("pledge_uzs")
        r.ai_verdict, r.win = info.get("ai_verdict") or "", info.get("win")
        r.advance_pct = info.get("advance_pct")
    regions = sorted({r.customer_region for r in live if r.customer_region})
    if src:
        live = [r for r in live if r.source == src]
    if region:
        live = [r for r in live if r.customer_region == region]
    if q:
        ql = q.lower()
        live = [r for r in live if ql in r.title.lower() or ql in (r.customer_name or "").lower()
                or ql in r.external_id or ql in (r.customer_region or "").lower()]
    live = tables.apply_num_filters(live, nf, lambda r: r.usd, lambda r: None, lambda r: None)
    in_kind = [r for r in live if r.kind == kind]
    shown, bands, keep = _chip_filters(request, in_kind, {"sm": lambda r: r.uzs, "zk": lambda r: r.pledge_uzs,
                                                          "av": lambda r: r.advance_pct},
                                       {"y": lambda r: r.direction in yset} if yset else None)
    dir_counts: dict[str, int] = {}
    for r in in_kind:
        if keep(r, "y"):
            dir_counts[r.direction] = dir_counts.get(r.direction, 0) + 1
    counts = {k: sum(r.kind == k and keep(r) for r in live) for k in ("goods", "service", "it")}
    shown = tables.sort_rows(shown, tables.CATALOG_COLS, sort, direction)
    next_scan = d.tenders.next_scan_at() if d.tenders else None
    return shown, {"kind": kind, "q": q, "src": src, "region": region, "regions": regions, "counts": counts,
                   "yon": yon, "dir_counts": dir_counts, **bands,
                   "updated": updated, "next_scan": next_scan, "nf": nf, "sort": sort, "direction": direction,
                   "infos": infos}


async def catalog_index(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    if request.query.get("k") == "it":  # IT lots have their own page
        raise web.HTTPSeeOther(ctx.url("/lots"))
    rows, p = await _query_catalog(request)
    infos = p.pop("infos")
    d = request.app[DEPS]
    pw = d.tenders.prices if d.tenders else None
    big = d.settings.config.tenders.prices.big_ratio
    uzum: list = []
    uzum_total = 0
    if pw:
        try:
            cmp_rows, _ = await pw.comparison()
            gaps = [r for r in cmp_rows if r.status == "ok" and (r.ratio >= big or 0 < r.ratio <= 1 / big)]
            ids = {r.id for r in rows}  # the big-gap list follows every filter of the page
            gaps = [r for r in gaps if r.lot.id in ids]
            uzum_total = len(gaps)
            uzum = sorted(gaps, key=lambda r: -abs(r.overpay))[:10]
            _set_lot_infos(infos, uzum)
        except Exception:  # the lot list must still open if the price data is broken
            log.exception("web.uzum_top_failed")
    return _page(request, ctx, views.catalog_page(ctx, rows, shown_limit=CATALOG_SHOWN, total=len(rows),
                                                  uzum=uzum, uzum_total=uzum_total, big=big, **p))


async def catalog_xlsx(request: web.Request) -> web.Response:
    rows, p = await _query_catalog(request)
    names = {"goods": "tovar_tenderlar", "service": "xizmat_tenderlar", "it": "it_tenderlar"}
    name = names.get(p["kind"], "barcha_tenderlar")
    return _xlsx_response(name, "Tenderlar", rows, tables.CATALOG_COLS)


# ── «Uzum narxlari»: tender unit prices vs Uzum Market ─────────────────────
async def _query_prices(request: web.Request) -> tuple[list, dict]:
    d = request.app[DEPS]
    pw = d.tenders.prices if d.tenders else None
    big = d.settings.config.tenders.prices.big_ratio
    f = request.query.get("f", "big")
    f = f if f in ("big", "over", "under", "all", "lots") else "big"
    yset = _directions(request)
    yon = ",".join(sorted(yset))
    q = request.query.get("q", "").strip()[:100]
    cols = tables.lot_price_cols(big) if f == "lots" else tables.price_cols(big)
    sort, direction = _sort_params(request, cols, "over" if f != "under" else "ratio",
                                   "desc" if f != "under" else "asc")
    rows, state = await pw.comparison() if pw else ([], {"phone": "o'chirilgan (config: tenders.prices)"})
    if pw:
        await _fill_lot_infos(pw, rows)
    rate = await d.tenders.fx.get() if d.tenders and d.tenders.fx else None
    for r in rows:
        lot = r.lot
        usd = tc.usd(lot.start_cost, lot.currency, rate)
        lot.uzs = lot.start_cost if lot.currency == "UZS" else (usd * rate if usd is not None and rate else None)
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r.item.name.lower() or ql in (r.lot.customer_name or "").lower()
                or ql in r.lot.external_id]
    dir_counts: dict[str, int] = {}
    for r in rows:
        dir_counts[r.lot.direction] = dir_counts.get(r.lot.direction, 0) + 1
    if yset:
        rows = [r for r in rows if r.lot.direction in yset]
    rows, bands, _ = _chip_filters(request, rows, {"sm": lambda r: r.lot.uzs, "zk": lambda r: r.lot.pledge_uzs,
                                                   "av": lambda r: r.lot.advance_pct})
    lots = group_by_lot(rows)
    ok = [r for r in rows if r.status == "ok"]
    groups = {"all": ok, "over": [r for r in ok if r.ratio >= big],
              "under": [r for r in ok if 0 < r.ratio <= 1 / big], "lots": lots}
    groups["big"] = groups["over"] + groups["under"]
    counts = {k: len(v) for k, v in groups.items()}
    shown = tables.sort_rows(groups[f], cols, sort, direction)
    next_run = pw.next_run_at() if pw else None
    return shown, {**bands, "f": f, "q": q, "yon": yon, "dir_counts": dir_counts, "counts": counts, "state": state,
                   "big": big, "sort": sort, "direction": direction, "next_run": next_run}


async def prices_index(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    rows, p = await _query_prices(request)
    return _page(request, ctx, views.prices_page(ctx, rows, shown_limit=CATALOG_SHOWN, **p))


async def prices_xlsx(request: web.Request) -> web.Response:
    rows, p = await _query_prices(request)
    if p["f"] == "lots":
        return _xlsx_response("uzum_lotlar", "Lotlar", rows, tables.lot_price_cols(p["big"]))
    return _xlsx_response("uzum_narxlari", "Uzum narxlari", rows, tables.price_cols(p["big"]))


async def _goods_lot(pw, lot_id: str):
    """One open goods lot with its item rows (none yet is fine: the page still shows the card and AI summary)."""
    if pw is None:
        return None
    rows, _ = await pw.comparison()
    mine = [r for r in rows if r.lot.id == lot_id]
    if mine:
        return group_by_lot(mine)[0]
    lot = next((r for r in await pw._open_goods() if r.id == lot_id), None)
    return LotPrices(lot, []) if lot else None


async def prices_lot(request: web.Request) -> web.Response:
    """Every item of one lot: tender price, Uzum price and why an item was not compared."""
    d = request.app[DEPS]
    ctx = _ctx(request)
    pw = d.tenders.prices if d.tenders else None
    lot_id = request.match_info["id"]
    lp = await _goods_lot(pw, lot_id)
    if lp is None:
        raise web.HTTPNotFound(text="Bunday ochiq tovar loti yo'q (yoki lot yopilgan).")
    detail = await pw.lot_detail(lp.lot)
    ai, busy = await pw.ai_summary(lp, detail)
    return _page(request, ctx, views.price_lot_page(ctx, lp, big=d.settings.config.tenders.prices.big_ratio,
                                                    detail=detail, ai=ai, ai_busy=busy))


async def prices_lot_ai(request: web.Request) -> web.Response:
    """«Qayta tahlil qilish»: prepare the AI summary of one goods lot again."""
    d, back = await _require_owner(request)
    pw = d.tenders.prices if d.tenders else None
    lot_id = request.match_info["id"]
    lp = await _goods_lot(pw, lot_id)
    if lp is None:
        raise web.HTTPNotFound()
    await pw.ai_summary(lp, await pw.lot_detail(lp.lot), fresh=True)
    raise web.HTTPSeeOther(f"{d.base}/all/uzum/lot/{lot_id}#ai")


# ── 🤖 AI chat about one lot ────────────────────────────────────────────────
def _chat(request: web.Request):
    d = request.app[DEPS]
    chat = getattr(d.tenders, "chat", None) if d.tenders else None
    if chat is None:
        raise web.HTTPNotFound(text="AI chat o'chirilgan (config.yaml → tenders.chat.enabled).")
    return d, chat


def _chat_key(request: web.Request) -> str:
    from app.tenders.chat import KEY_RE

    key = request.match_info["key"]
    if not KEY_RE.match(key):
        raise web.HTTPNotFound()
    return key


async def _chat_index(request: web.Request, *, value: str = "", error: str = "", status: int = 200) -> web.Response:
    d, chat = _chat(request)
    ctx = _ctx(request)
    now = now_utc()
    it_lots = [lot for lot in await d.tenders.open_lots(now)][:12]
    return _html(views.chat_index_page(ctx, await chat.recent(), it_lots, value=value, error=error), status)


async def chat_index(request: web.Request) -> web.Response:
    return await _chat_index(request)


async def chat_open(request: web.Request) -> web.Response:
    from app.tenders.chat import ChatError

    d, back = await _require_owner(request)
    _, chat = _chat(request)
    form = await request.post()
    url = str(form.get("url") or "").strip()[:500]
    try:
        key = await chat.open(url, fresh=bool(form.get("fresh")))
    except ChatError as e:
        return await _chat_index(request, value=url, error=str(e), status=400)
    raise web.HTTPSeeOther(f"{d.base}/chat/{key}")


async def chat_new(request: web.Request) -> web.Response:
    """«🤖 AI'dan so'rash» links on lot pages: GET with ?ref=<source>-<id> (opening only reads the lot)."""
    from app.tenders.chat import ChatError

    d, chat = _chat(request)
    ref = request.query.get("ref", "")[:500]
    try:
        key = await chat.open(ref)
    except ChatError as e:
        return await _chat_index(request, value=ref, error=str(e), status=400)
    raise web.HTTPSeeOther(f"{d.base}/chat/{key}")


async def chat_view(request: web.Request) -> web.Response:
    d, chat = _chat(request)
    key = _chat_key(request)
    data = await chat.get(key)
    if data is None:
        raise web.HTTPSeeOther(f"{d.base}/chat/new?" + urlencode({"ref": key}))
    data = await chat.resume(key, data)
    ctx = _ctx(request)
    return _page(request, ctx, views.chat_page(ctx, key, data))


async def chat_ask(request: web.Request) -> web.Response:
    """Without JS: a normal form post that waits for the answer. With JS (X-Requested-With: fetch): JSON."""
    from app.tenders.chat import ChatError

    d, _ = await _require_owner(request)
    _, chat = _chat(request)
    key = _chat_key(request)
    form = await request.post()
    question = str(form.get("q") or "")
    is_fetch = request.headers.get("X-Requested-With") == "fetch"
    try:
        answer = await chat.ask(key, question)
    except ChatError as e:
        if is_fetch:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        raise _flash_redirect(f"{d.base}/chat/{key}", str(e)) from None
    if is_fetch:
        return web.json_response({"ok": True, "html": views.chat_message(answer)})
    raise web.HTTPSeeOther(f"{d.base}/chat/{key}#end")


async def chat_clear(request: web.Request) -> web.Response:
    d, _ = await _require_owner(request)
    _, chat = _chat(request)
    key = _chat_key(request)
    form = await request.post()
    if form.get("what") == "reload":
        await chat.open(key, fresh=True)
    else:
        await chat.clear(key)
    raise web.HTTPSeeOther(f"{d.base}/chat/{key}")


# ── Owner auth + actions ────────────────────────────────────────────────────
def _safe_next(d: Deps, value: str | None) -> str:
    home = f"{d.base}/"
    if not value or not value.startswith(home) or value.startswith("//") or "\\" in value:
        return home
    return value


async def login_form(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    return _html(views.login_page(_ctx(request), next_url=_safe_next(d, request.query.get("next"))))


async def login_submit(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    form = await request.post()
    ip = _ip(request)
    nxt = _safe_next(d, str(form.get("next") or ""))
    user = str(form.get("user") or "")[:64]
    if d.auth.locked(ip):
        return _html(views.login_page(_ctx(request), error="Juda ko'p urinish. 15 daqiqadan keyin qayta urining.",
                                      user=user), 429)
    if not d.auth.try_login(ip, user, str(form.get("password") or "")):
        log.warning("web.login_failed", extra={"ip": ip})
        return _html(views.login_page(_ctx(request), error="Login yoki parol noto'g'ri.", next_url=nxt, user=user), 403)
    log.info("web.login_ok", extra={"ip": ip})
    resp = web.HTTPSeeOther(nxt)
    secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https" or "CF-Connecting-IP" in request.headers
    resp.set_cookie(COOKIE, d.auth.issue(), max_age=SESSION_TTL_S, httponly=True, samesite="Strict",
                    secure=secure, path=f"{d.base}/" if d.base else "/")
    raise resp


async def _require_owner(request: web.Request) -> tuple[Deps, str]:
    """Valid owner session + CSRF token, else stop. Returns deps and the safe page to go back to."""
    d = request.app[DEPS]
    exp = d.auth.session_exp(request.cookies.get(COOKIE))
    form = await request.post()
    if exp is None:
        raise web.HTTPSeeOther(f"{d.base}/login")
    if not d.auth.check_csrf(exp, str(form.get("csrf") or "")):
        raise web.HTTPForbidden(text="CSRF token noto'g'ri — sahifani yangilab qayta urining.")
    return d, _safe_next(d, str(form.get("back") or ""))


async def logout(request: web.Request) -> web.Response:
    d, _ = await _require_owner(request)
    resp = web.HTTPSeeOther(f"{d.base}/login")
    resp.del_cookie(COOKIE, path=f"{d.base}/" if d.base else "/")
    raise resp


async def set_theme(request: web.Request) -> web.Response:
    d, back = await _require_owner(request)
    form = await request.post()
    theme = str(form.get("theme") or "")
    if theme == "toggle":
        theme = "light" if str(form.get("cur") or "") == "dark" else "dark"
    if theme not in views.THEMES:
        raise web.HTTPBadRequest(text="Noma'lum rejim")
    resp = web.HTTPSeeOther(back)
    resp.set_cookie(THEME, theme, max_age=365 * 24 * 3600, path=f"{d.base}/" if d.base else "/",
                    httponly=True, samesite="Lax", secure=request.headers.get("X-Forwarded-Proto") == "https")
    raise resp


async def profile(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    ctx = _ctx(request)
    exp = d.auth.session_exp(request.cookies.get(COOKIE)) or 0
    from datetime import datetime as _dt
    until = _dt.fromtimestamp(exp, TASHKENT).strftime("%d.%m.%Y %H:%M")
    async with d.sessionmaker() as s:
        lots = await s.scalar(select(func.count(Lot.id))) or 0
        active = await s.scalar(select(func.count(Lot.id)).where(Lot.status.in_(ACTIVE))) or 0
        jobs = await s.scalar(select(func.count(Job.id))) or 0
    stats = [("Jami tenderlar", lots), ("Faol tenderlar", active), ("Frilans ishlar", jobs)]
    return _page(request, ctx, views.profile_page(ctx, user=d.auth.user, session_until=until, stats=stats))


async def _notify(d: Deps, text: str) -> None:
    if d.notify is None:
        return
    try:
        await d.notify(text)
    except Exception:
        log.exception("web.notify_failed")


async def lot_action(request: web.Request) -> web.Response:
    d, back = await _require_owner(request)
    action = request.match_info["action"]
    if d.tenders is None or action not in LOT_ACTIONS:
        raise web.HTTPNotFound()
    status, label = LOT_ACTIONS[action]
    lot = await d.tenders.set_status(int(request.match_info["id"]), status)
    if lot is None:
        raise web.HTTPNotFound()
    log.info("web.lot_action", extra={"lot": lot.external_id, "action": action})
    await _notify(d, f"🌐 Saytdan: lot <b>{views.h(lot.external_id)}</b> — {label}")
    raise _flash_redirect(back, f"Lot {lot.external_id}: {label}")


async def job_action(request: web.Request) -> web.Response:
    d, back = await _require_owner(request)
    action = request.match_info["action"]
    job_id = int(request.match_info["id"])
    if d.freelance is None or action not in ("approve", "skip"):
        raise web.HTTPNotFound()
    owner = d.settings.owner_chat_id
    if action == "approve":
        res = await d.freelance.approve(job_id, owner)
        msg = {"manual": "✅ Tasdiqlandi. Taklifni platformaga o'zingiz joylang (havola sahifada).",
               "queued": "✅ Tasdiqlandi, yuborish navbatiga qo'yildi."}.get(res, res)
    else:
        res = await d.freelance.skip(job_id, owner)
        msg = "⏭ O'tkazib yuborildi." if res == "ok" else res
    log.info("web.job_action", extra={"job": job_id, "action": action, "result": res})
    if res in ("manual", "queued", "ok"):
        await _notify(d, f"🌐 Saytdan: ish #{job_id} — {msg}")
    raise _flash_redirect(back, msg)


async def lots_bulk(request: web.Request) -> web.Response:
    d, back = await _require_owner(request)
    form = await request.post()
    action = str(form.get("action") or "")
    if d.tenders is None or action not in LOT_ACTIONS:
        raise web.HTTPBadRequest(text="Noma'lum amal")
    status, label = LOT_ACTIONS[action]
    done = []
    for raw in form.getall("ids", [])[:500]:
        if str(raw).isdigit() and (lot := await d.tenders.set_status(int(raw), status)) is not None:
            done.append(lot.external_id)
    log.info("web.lots_bulk", extra={"action": action, "count": len(done)})
    if done:
        await _notify(d, f"🌐 Saytdan: {len(done)} ta lot — {label}: " + ", ".join(views.h(x) for x in done[:30]))
    raise _flash_redirect(back, f"{len(done)} ta lot: {label}")


async def jobs_bulk(request: web.Request) -> web.Response:
    d, back = await _require_owner(request)
    form = await request.post()
    action = str(form.get("action") or "")
    if d.freelance is None or action not in ("approve", "skip"):
        raise web.HTTPBadRequest(text="Noma'lum amal")
    owner = d.settings.owner_chat_id
    ok, failed = 0, 0
    for raw in form.getall("ids", [])[:500]:
        if not str(raw).isdigit():
            continue
        fn = d.freelance.approve if action == "approve" else d.freelance.skip
        res = await fn(int(raw), owner)
        if res in ("manual", "queued", "ok"):
            ok += 1
        else:
            failed += 1
    label = "✅ tasdiqlandi" if action == "approve" else "⏭ o'tkazib yuborildi"
    log.info("web.jobs_bulk", extra={"action": action, "ok": ok, "failed": failed})
    if ok:
        await _notify(d, f"🌐 Saytdan: {ok} ta ish {label}")
    msg = f"{ok} ta ish {label}" + (f", {failed} tasini bu holatda o'zgartirib bo'lmadi" if failed else "")
    if action == "approve" and ok:
        msg += ". Takliflarni platformaga o'zingiz joylang."
    raise _flash_redirect(back, msg)


async def app_js(_: web.Request) -> web.Response:
    return web.Response(body=APP_JS, content_type="application/javascript", charset="utf-8",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app(settings: Settings, sessionmaker: async_sessionmaker, *, tenders: TenderService | None,
              freelance: FreelanceService | None, notify: Callable[[str], Awaitable[None]] | None = None) -> web.Application:
    wcfg = settings.config.web
    pw = settings.secrets.web_admin_password
    auth = Auth(pw.get_secret_value() if pw else None, settings.secrets.telegram_bot_token.get_secret_value(),
                user=settings.secrets.web_admin_user)
    app = web.Application(middlewares=[security_headers, require_login], client_max_size=64 * 1024)
    app[DEPS] = Deps(settings, sessionmaker, tenders, freelance, notify, auth, wcfg.base_path, wcfg.title)
    b = wcfg.base_path
    if b:
        async def add_slash(_: web.Request) -> web.Response:
            raise web.HTTPMovedPermanently(f"{b}/")

        app.router.add_get(b, add_slash)
    app.router.add_get(f"{b}/", orders_index)
    app.router.add_get(f"{b}/orders.xlsx", orders_xlsx)
    app.router.add_get(f"{b}/feed", feed_index)
    app.router.add_get(f"{b}/all", catalog_index)
    app.router.add_get(f"{b}/all.xlsx", catalog_xlsx)
    app.router.add_get(f"{b}/all/uzum", prices_index)
    app.router.add_get(f"{b}/all/uzum.xlsx", prices_xlsx)
    app.router.add_get(f"{b}/all/uzum/lot/{{id}}", prices_lot)
    app.router.add_post(f"{b}/all/uzum/lot/{{id}}/ai", prices_lot_ai)
    app.router.add_get(f"{b}/lots", tenders_index)
    app.router.add_get(f"{b}/lots.xlsx", tenders_xlsx)
    app.router.add_post(f"{b}/lots/bulk", lots_bulk)
    app.router.add_post(f"{b}/jobs/bulk", jobs_bulk)
    app.router.add_get(f"{b}/static/app.js", app_js)
    app.router.add_get(f"{b}/jobs.xlsx", jobs_xlsx)
    app.router.add_get(f"{b}/lot/{{id}}", lot_detail)
    app.router.add_post(f"{b}/lot/{{id:\\d+}}/{{action}}", lot_action)
    app.router.add_get(f"{b}/jobs", jobs_index)
    app.router.add_get(f"{b}/job/{{id}}", job_detail)
    app.router.add_post(f"{b}/job/{{id:\\d+}}/{{action}}", job_action)
    app.router.add_get(f"{b}/login", login_form)
    app.router.add_post(f"{b}/login", login_submit)
    app.router.add_post(f"{b}/logout", logout)
    app.router.add_post(f"{b}/theme", set_theme)
    app.router.add_get(f"{b}/profile", profile)
    app.router.add_get(f"{b}/chat", chat_index)
    app.router.add_post(f"{b}/chat", chat_open)
    app.router.add_get(f"{b}/chat/new", chat_new)
    key = "{key:[a-z_]+-\\d+}"
    app.router.add_get(f"{b}/chat/{key}", chat_view)
    app.router.add_post(f"{b}/chat/{key}/ask", chat_ask)
    app.router.add_post(f"{b}/chat/{key}/clear", chat_clear)
    app.router.add_get(f"{b}/healthz", healthz)
    return app


async def start(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("web.started", extra={"host": host, "port": port})
    return runner
