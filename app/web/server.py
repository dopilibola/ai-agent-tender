"""🌐 Read-only web view of tender lots, served by the bot process itself (aiohttp ships with aiogram).

Routes:
  GET /                 → redirect to /tender/lots
  GET /tender/lots      → HTML table (?all=1 also shows ignored/closed lots)
  GET /tender/lots.json → same data as JSON
  GET /health           → "ok" (no auth; for tunnels / uptime checks)

If WEB_TOKEN is set in .env, every page except /health needs ?key=<WEB_TOKEN> once;
after that a cookie keeps you logged in.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime
from html import escape
from typing import Any

from aiohttp import web
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.models import Lot, LotStatus
from app.tenders.cards import STATUS_UZ, VERDICT_BADGE, fmt_usd, money_uzs, usd
from app.tenders.timeutil import fmt_local, now_utc, remaining_uz

log = logging.getLogger(__name__)

COOKIE = "tender_key"
HIDDEN = (LotStatus.IGNORED, LotStatus.CLOSED)
MAX_ROWS = 500

SESSIONMAKER = web.AppKey("sessionmaker", async_sessionmaker)
TOKEN = web.AppKey("token", str)


def _authorized(request: web.Request) -> bool:
    token = request.app[TOKEN]
    if not token:
        return True
    given = request.query.get("key") or request.cookies.get(COOKIE) or ""
    return hmac.compare_digest(given.encode(), token.encode())


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path == "/health":
        return await handler(request)
    if not _authorized(request):
        return web.Response(status=401, text="401: kalit kerak (?key=...)")
    response = await handler(request)
    if request.app[TOKEN] and "key" in request.query:
        response.set_cookie(COOKIE, request.query["key"], max_age=90 * 86400, httponly=True, samesite="Lax")
    return response


async def _load_lots(request: web.Request, show_all: bool) -> list[Lot]:
    stmt = select(Lot)
    if not show_all:
        stmt = stmt.where(Lot.status.not_in(HIDDEN))
    # Open lots first (soonest deadline on top), lots without a deadline last.
    stmt = stmt.order_by(case((Lot.deadline_at.is_(None), 1), else_=0), Lot.deadline_at, Lot.id.desc()).limit(MAX_ROWS)
    async with request.app[SESSIONMAKER]() as session:
        return list((await session.scalars(stmt)).all())


def _lot_dict(lot: Lot, now: datetime) -> dict[str, Any]:
    a = lot.analysis or {}
    return {
        "id": lot.id,
        "source": lot.source,
        "lot_no": lot.external_id,
        "url": lot.url,
        "title": lot.title,
        "customer": lot.customer_name,
        "region": lot.customer_region,
        "start_cost": lot.start_cost,
        "currency": lot.currency,
        "start_cost_usd": usd(lot.start_cost, lot.currency, lot.usd_rate),
        "pledge_amount": lot.pledge_amount,
        "deadline": fmt_local(lot.deadline_at) if lot.deadline_at else None,
        "remaining": remaining_uz(lot.deadline_at, now),
        "status": lot.status.value,
        "verdict": lot.verdict,
        "fit_score": a.get("fit_score"),
    }


async def lots_json(request: web.Request) -> web.Response:
    now = now_utc()
    lots = await _load_lots(request, request.query.get("all") == "1")
    return web.json_response({"count": len(lots), "lots": [_lot_dict(lot, now) for lot in lots]})


def _row(lot: Lot, now: datetime) -> str:
    d = _lot_dict(lot, now)
    verdict = VERDICT_BADGE.get(lot.verdict or "", escape(lot.verdict or "—"))
    fit = f"<br><small>fit {d['fit_score']}/100</small>" if d["fit_score"] is not None else ""
    expired = "muddati o'tgan" in d["remaining"]
    return (
        f"<tr class='{'old' if expired else ''}'>"
        f"<td><a href='{escape(lot.url, quote=True)}' target='_blank' rel='noopener'>{escape(lot.external_id)}</a>"
        f"<br><small>{escape(lot.source)}</small></td>"
        f"<td>{escape(lot.title)}<br><small>{escape(lot.customer_name or '')}"
        f"{' · ' + escape(lot.customer_region) if lot.customer_region else ''}</small></td>"
        f"<td class='num'>{escape(money_uzs(lot.start_cost, lot.currency))}"
        f"<br><small>{escape(fmt_usd(d['start_cost_usd']))}</small></td>"
        f"<td>{escape(d['deadline'] or '—')}<br><small>{escape(d['remaining'])}</small></td>"
        f"<td>{verdict}{fit}</td>"
        f"<td>{escape(STATUS_UZ.get(lot.status, lot.status.value))}</td>"
        "</tr>"
    )


PAGE = """<!DOCTYPE html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🏛 Tender lotlar</title>
<style>
 body{{font:14px/1.4 system-ui,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#111}}
 h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#666;margin-bottom:12px}}
 table{{width:100%;border-collapse:collapse;background:#fff}}
 th,td{{padding:8px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}
 th{{background:#f0f2f5;position:sticky;top:0}} small{{color:#666}} .num{{white-space:nowrap}}
 tr.old{{opacity:.5}} a{{color:#0b5bd3}}
 @media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}table{{background:#1a1a1a}}
  th{{background:#222}}th,td{{border-color:#333}}small,.meta{{color:#999}}a{{color:#6aa8ff}}}}
</style></head><body>
<h1>🏛 Tender lotlar</h1>
<div class="meta">{count} ta lot · {now} (Toshkent) · <a href="{toggle_href}">{toggle_text}</a></div>
<table><thead><tr><th>Lot</th><th>Predmet / buyurtmachi</th><th>Narx</th><th>Muddat</th>
<th>AI xulosa</th><th>Holat</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


async def lots_page(request: web.Request) -> web.Response:
    show_all = request.query.get("all") == "1"
    now = now_utc()
    lots = await _load_lots(request, show_all)
    rows = "".join(_row(lot, now) for lot in lots) or "<tr><td colspan='6'>Hozircha lot yo'q.</td></tr>"
    html = PAGE.format(
        count=len(lots),
        now=fmt_local(now),
        toggle_href="?" if show_all else "?all=1",
        toggle_text="faqat faol lotlar" if show_all else "hammasini ko'rsatish",
        rows=rows,
    )
    return web.Response(text=html, content_type="text/html")


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def index(_: web.Request) -> web.Response:
    raise web.HTTPFound("/tender/lots")


def build_app(sessionmaker: async_sessionmaker, token: str | None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[SESSIONMAKER] = sessionmaker
    app[TOKEN] = token or ""
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/tender/lots", lots_page)
    app.router.add_get("/tender/lots.json", lots_json)
    return app


async def start_web(settings: Settings, sessionmaker: async_sessionmaker) -> web.AppRunner | None:
    cfg = settings.config.web
    if not cfg.enabled:
        return None
    token = settings.secrets.web_token.get_secret_value() if settings.secrets.web_token else None
    runner = web.AppRunner(build_app(sessionmaker, token), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, cfg.host, cfg.port).start()
    except OSError as e:  # port busy (e.g. another server on 8080) — the bot must keep running
        log.error("web.start_failed", extra={"port": cfg.port, "error": str(e)})
        await runner.cleanup()
        return None
    log.info("web.started", extra={"host": cfg.host, "port": cfg.port, "auth": bool(token)})
    return runner
