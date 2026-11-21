"""Private dashboard (aiohttp, runs inside the bot process and shares its DB/services).

Every page needs a login (WEB_ADMIN_USER / WEB_ADMIN_PASSWORD from .env). After login the site shows the same
messages the bot sends to Telegram, plus tender / freelance lists and their buttons.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.feed import KINDS
from app.config import Settings
from app.core.states import JobStatus
from app.db.models import Job, Lot, LotStatus, SentMessage
from app.pipeline.service import FreelanceService
from app.tenders.filters import is_it_lot
from app.tenders.service import ACTIVE, TenderService
from app.tenders.timeutil import TASHKENT, as_utc, now_utc
from app.web import tables, views, xlsx
from app.web.auth import COOKIE, SESSION_TTL_S, Auth

FLASH = "fh_flash"

log = logging.getLogger(__name__)

S = JobStatus
# Lots page filters: ?z= (zakalat, USD) and ?d= (deadline within N days)
PLEDGE_MAX = {"0": 0.0, "500": 500.0, "1000": 1000.0, "3000": 3000.0, "10000": 10000.0}
DEADLINE_DAYS = ("1", "3", "7", "14", "30")
HIDDEN_JOB = (S.SKIPPED, S.FOUND, S.EXPIRED)
VERDICT_KEY = {"good": "QATNASHAMIZ", "maybe": "SHARTLI", "bad": "YO'Q"}
LOT_ACTIONS = {"bid": (LotStatus.BIDDING, "📌 Qatnashamiz"), "unbid": (LotStatus.NOTIFIED, "↩️ Qatnashmaymiz"),
               "ignore": (LotStatus.IGNORED, "🚫 Kerak emas")}
MAX_ROWS = 300


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
                     here=request.path_qs, flash=flash)


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
        "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'",
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
    now = now_utc()
    async with d.sessionmaker() as s:
        stmt = select(Lot)
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
        open_all = (await s.scalars(select(Lot).where(Lot.status.in_(ACTIVE)))).all()
    kf = d.tenders.filter if d.tenders else None
    lots = [lot for lot in lots if is_it_lot(lot, kf)]  # only IT lots on the site
    open_all = [lot for lot in open_all if is_it_lot(lot, kf)]
    if status != "all":
        lots = [lot for lot in lots if not lot.deadline_at or as_utc(lot.deadline_at) > now]
    pledge = request.query.get("z", "")
    if pledge in PLEDGE_MAX or pledge == "limit":
        cap = d.tenders.limit_usd if pledge == "limit" and d.tenders else PLEDGE_MAX.get(pledge, 0.0)
        lots = [lot for lot in lots if (v := tables.pledge_value_usd(lot)) is not None and v <= cap]
    else:
        pledge = ""
    days = request.query.get("d", "")
    if days in DEADLINE_DAYS:
        until = now + timedelta(days=int(days))
        lots = [lot for lot in lots if lot.deadline_at and now < as_utc(lot.deadline_at) <= until]
    else:
        days = ""
    lots = tables.sort_rows(lots, tables.LOT_COLS, sort, direction)
    live = [lot for lot in open_all if not lot.deadline_at or as_utc(lot.deadline_at) > now]
    counts = {
        "open": len(live),
        "good": sum(lot.verdict == "QATNASHAMIZ" for lot in live),
        "maybe": sum(lot.verdict == "SHARTLI" for lot in live),
        "bidding": sum(lot.status == LotStatus.BIDDING for lot in live),
    }
    return lots, {"q": q, "verdict": verdict, "status": status, "pledge": pledge, "days": days, "counts": counts,
                  "sort": sort,
                  "direction": direction}


async def tenders_index(request: web.Request) -> web.Response:
    d = request.app[DEPS]
    lots, p = await _query_lots(request)
    health = await d.tenders.health() if d.tenders else {}
    ctx = _ctx(request)
    limit = d.tenders.limit_usd if d.tenders else 0.0
    return _page(request, ctx, views.tenders_page(ctx, lots, health=health, limit_usd=limit, **p))


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
    async with d.sessionmaker() as s:
        stmt = select(Job)
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
    jobs = tables.sort_rows(jobs, tables.JOB_COLS, sort, direction)
    return jobs, {"q": q, "platform": platform, "show": show, "platforms": platforms, "counts": counts,
                  "sort": sort, "direction": direction}


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
                        headers={"Cache-Control": "public, max-age=300"})


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
    app.router.add_get(f"{b}/", feed_index)
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
    app.router.add_get(f"{b}/healthz", healthz)
    return app


async def start(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("web.started", extra={"host": host, "port": port})
    return runner
