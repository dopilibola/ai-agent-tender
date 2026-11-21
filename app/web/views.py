"""HTML rendering for the dashboard (Uzbek). Everything from the DB is untrusted → always escaped via h()."""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import urlencode

from app.bot.cards import RISK_UZ, ago_uz, money
from app.core.states import STATUS_LABEL_UZ, JobStatus
from app.db.models import Job, Lot, LotStatus, Proposal
from app.tenders import cards as tc
from app.tenders.timeutil import as_utc, fmt_local, now_utc, remaining_uz

VERDICT_CLASS = {"QATNASHAMIZ": "good", "SHARTLI": "maybe", "YO'Q": "bad"}
PLATFORM_NAME = {"fl_ru": "FL.ru", "kwork": "Kwork", "freelancer_com": "Freelancer.com", "email": "Upwork/Fiverr",
                 "upwork": "Upwork", "fiverr": "Fiverr", "truelancer": "Truelancer"}


def h(value) -> str:
    return escape(str(value), quote=True) if value is not None else ""


def safe_url(url: str | None) -> str | None:
    return url if url and url.startswith(("https://", "http://")) else None


def ext_link(url: str | None, text: str, cls: str = "") -> str:
    """Link to the original site (new tab, no referrer/opener)."""
    u = safe_url(url)
    if not u:
        return h(text)
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{h(u)}" target="_blank" rel="noopener noreferrer nofollow">{h(text)}</a>'


class Ctx:
    """What every page needs: paths, title, admin session."""

    def __init__(self, base: str, title: str, admin_exp: int | None, csrf: str | None, login_enabled: bool,
                 here: str = "", flash: str = ""):
        self.base, self.title = base, title
        self.admin = admin_exp is not None
        self.csrf = csrf
        self.login_enabled = login_enabled
        self.here = here    # current path+query: where an action returns to
        self.flash = flash  # one-shot message after an action

    def url(self, path: str = "/", **query) -> str:
        q = {k: v for k, v in query.items() if v not in (None, "")}
        return f"{self.base}{path}" + (f"?{urlencode(q)}" if q else "")


CSS = """
:root{color-scheme:dark;--bg:#0b0f17;--side:#0e131d;--card:#121826;--card2:#161d2d;--ink:#e6eaf2;--muted:#8b95a7;
--line:#1f2738;--line2:#262f43;--brand:#4f8cff;--brand2:#3b74e6;--good:#3ecf8e;--good-bg:rgba(62,207,142,.14);
--maybe:#f5b544;--maybe-bg:rgba(245,181,68,.14);--bad:#ff6b6b;--bad-bg:rgba(255,107,107,.15);--chip:#1b2335;
--blue-bg:rgba(79,140,255,.15)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}.app>div{min-width:0}
aside{background:var(--side);border-right:1px solid var(--line);padding:18px 12px;position:sticky;top:0;height:100vh;
display:flex;flex-direction:column;gap:4px}
.brand{font-weight:700;font-size:15px;color:var(--ink);padding:4px 10px 16px;display:block;line-height:1.3}
.brand small{display:block;color:var(--muted);font-weight:500;font-size:12px}
aside nav{display:flex;flex-direction:column;gap:2px}
aside nav a{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;color:var(--muted);font-weight:600}
aside nav a:hover{background:var(--chip);color:var(--ink);text-decoration:none}
aside nav a.on{background:var(--blue-bg);color:var(--brand)}
aside .grow{flex:1}aside form button{width:100%}
main{padding:22px 28px 60px;min-width:0}
h1{font-size:22px;margin:0 0 4px;font-weight:700}h2{font-size:16px;margin:22px 0 8px}
.sub{color:var(--muted);margin:0 0 16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:0 0 18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.stat b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.stat span{color:var(--muted);font-size:13px}
form.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;align-items:center}
input,select,button,textarea{font:inherit;color:inherit}
input[type=text],input[type=search],input[type=password],select{background:var(--card);border:1px solid var(--line2);
border-radius:8px;padding:8px 11px;outline:none}
input:focus,select:focus{border-color:var(--brand)}
input[type=search]{flex:1;min-width:220px}
button,.btn{background:var(--brand);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-weight:600;
cursor:pointer;display:inline-block;white-space:nowrap}
button:hover,.btn:hover{background:var(--brand2);text-decoration:none}
.btn.ghost,button.ghost{background:var(--chip);color:var(--ink);border:1px solid var(--line2)}
.btn.ghost:hover,button.ghost:hover{background:var(--line2)}
button.danger{background:var(--bad-bg);color:var(--bad)}
.badge,.pill{display:inline-block;border-radius:6px;padding:3px 9px;font-weight:700;font-size:12px;white-space:nowrap;
background:var(--chip);color:var(--muted)}
.badge.good,.pill.good{background:var(--good-bg);color:var(--good)}
.badge.maybe,.pill.maybe{background:var(--maybe-bg);color:var(--maybe)}
.badge.bad,.pill.bad{background:var(--bad-bg);color:var(--bad)}
.pill.blue{background:var(--blue-bg);color:var(--brand)}
.pill.solid-bad{background:#e5534b;color:#fff}
.chip{background:var(--chip);border-radius:6px;padding:1px 7px;font-size:12px;color:var(--muted)}
.lotno{font-weight:700;font-variant-numeric:tabular-nums}
.urgent{color:var(--bad)!important;font-weight:700}.soon{color:var(--maybe)!important;font-weight:700}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 12px}
dl.kv{display:grid;grid-template-columns:minmax(120px,200px) 1fr;gap:8px 16px;margin:0}
dl.kv dt{color:var(--muted)}dl.kv dd{margin:0}
ul{margin:4px 0;padding-left:20px}li{margin:2px 0}
.prose{white-space:pre-wrap;word-wrap:break-word}
.muted{color:var(--muted)}.empty{padding:40px;text-align:center;color:var(--muted)}
.flash{background:var(--good-bg);color:var(--good);border-radius:10px;padding:10px 14px;margin:0 0 14px;font-weight:600}
.flash.err{background:var(--bad-bg);color:var(--bad)}
footer{padding:0 28px 24px;color:var(--muted);font-size:12px}
.count{color:var(--muted);margin:4px 0 10px;font-size:15px}
.bulk{display:none;gap:8px;align-items:center;margin:0 0 10px;padding:8px 12px;background:var(--card2);
border:1px solid var(--line2);border-radius:10px}.bulk.show{display:flex}
.tablewrap{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:14px;max-height:calc(100vh - 150px)}
table.grid{border-collapse:separate;border-spacing:0;width:100%;font-size:14px}
.grid th,.grid td{padding:13px 14px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;
vertical-align:middle}
.grid thead th{position:sticky;top:0;background:var(--card2);font-weight:600;color:var(--ink);z-index:2}
.grid th a{color:var(--ink);display:flex;align-items:center;justify-content:space-between;gap:10px}
.grid th a:hover{text-decoration:none;color:var(--brand)}
.grid th .si{color:var(--muted);flex:none}.grid th.on .si,.grid th.on a{color:var(--brand)}
.grid tbody tr{cursor:pointer}.grid tbody tr:hover td{background:rgba(255,255,255,.025)}
.grid tbody tr.sel td{background:var(--blue-bg)}
.grid td.num,.grid th.num{text-align:right}.grid th.num a{justify-content:flex-end}
.grid td.num{font-variant-numeric:tabular-nums}
.grid td.wide{max-width:360px;overflow:hidden;text-overflow:ellipsis}
.grid td.mid{max-width:220px;overflow:hidden;text-overflow:ellipsis}
.grid td.dim{color:var(--muted);font-variant-numeric:tabular-nums}
.grid .cb{width:44px;padding-right:0}
.grid input[type=checkbox]{appearance:none;width:20px;height:20px;border:2px solid var(--line2);border-radius:6px;
background:transparent;cursor:pointer;vertical-align:middle;margin:0;display:inline-grid;place-content:center}
.grid input[type=checkbox]:checked{background:var(--brand);border-color:var(--brand)}
.grid input[type=checkbox]:checked::after{content:"";width:9px;height:5px;border:2px solid #fff;border-top:0;
border-right:0;transform:rotate(-45deg) translate(1px,-1px)}
.nowrap{white-space:nowrap}
.feed{display:grid;gap:12px;max-width:760px}
.msg{background:var(--card);border:1px solid var(--line);border-radius:14px 14px 14px 4px;padding:11px 15px 9px}
.msg .body{white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
.msg .body pre,.msg .body code{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:var(--chip);
border-radius:5px;padding:0 4px}
.msg .body blockquote{margin:4px 0;padding-left:10px;border-left:3px solid var(--brand)}
.msg .when{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:12px;margin-top:6px}
.kb{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.kb a{flex:1 1 auto;text-align:center;background:var(--chip);border-radius:8px;padding:6px 10px;font-weight:600;font-size:13px}
.spoiler{background:var(--muted);color:transparent;border-radius:3px}.spoiler:hover{color:inherit;background:none}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}
.tabs a{padding:6px 13px;border-radius:8px;background:var(--card);border:1px solid var(--line);color:var(--muted);
font-weight:600;font-size:13px}.tabs a.on{background:var(--blue-bg);border-color:transparent;color:var(--brand)}
.login{max-width:380px;margin:12vh auto 0;display:grid;gap:12px;padding:26px}
.login label{display:grid;gap:4px;color:var(--muted);font-size:13px}.login button{padding:10px}
.solo main{max-width:none}
@media (max-width:900px){.app{grid-template-columns:1fr}aside{position:static;height:auto;flex-direction:row;
flex-wrap:wrap;align-items:center;padding:10px}.brand{padding:4px 10px}aside nav{flex-direction:row;flex-wrap:wrap}
aside .grow{display:none}main{padding:16px}dl.kv{grid-template-columns:1fr}}
"""


def layout(ctx: Ctx, active: str, body: str, *, title: str = "", flash: str = "", error: bool = False) -> str:
    tabs = [("feed", ctx.url("/"), "📨", "Telegram xabarlari"), ("tenders", ctx.url("/lots"), "🏛", "Tenderlar"),
            ("jobs", ctx.url("/jobs"), "💼", "Frilans ishlar")]
    flash = flash or ctx.flash
    flash_html = f'<div class="flash{" err" if error else ""}">{h(flash)}</div>' if flash else ""
    page_title = f"{title} — {ctx.title}" if title else ctx.title
    head = f"""<!DOCTYPE html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{h(page_title)}</title><style>{CSS}</style>
<script src="{h(ctx.url("/static/app.js"))}" defer></script></head>"""
    if not ctx.admin:  # login screen: nothing but the form
        return f'{head}<body class="solo"><main>{flash_html}{body}</main></body></html>'
    nav = "".join(f'<a class="{"on" if key == active else ""}" href="{h(url)}"><span>{icon}</span>{h(label)}</a>'
                  for key, url, icon, label in tabs)
    logout = (f'<form method="post" action="{h(ctx.url("/logout"))}">'
              f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
              '<button class="ghost" type="submit">Chiqish</button></form>')
    return f"""{head}<body><div class="app">
<aside><a class="brand" href="{h(ctx.url("/"))}">{h(ctx.title)}<small>AI agent · CRM</small></a>
<nav>{nav}</nav><div class="grow"></div>{logout}</aside>
<div><main>{flash_html}{body}</main>
<footer>Tenderlar har 30 daqiqada, frilans ishlar har 5–10 daqiqada yangilanadi. Vaqtlar — Toshkent vaqti.
Sahifa: {h(fmt_local(now_utc()))}</footer></div></div></body></html>"""


def _action(ctx: Ctx, path: str, label: str, cls: str = "") -> str:
    return (f'<form method="post" action="{h(ctx.url(path))}" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
            f'<input type="hidden" name="back" value="{h(ctx.here)}">'
            f'<button class="{cls}" type="submit">{h(label)}</button></form>')


def _deadline(lot: Lot, now: datetime) -> str:
    if not lot.deadline_at:
        return '<span class="muted">muddat noma\'lum</span>'
    left = (as_utc(lot.deadline_at) - now).total_seconds()  # type: ignore[operator]
    cls = "urgent" if 0 < left < 86400 else "soon" if 0 < left < 3 * 86400 else ""
    return f'<span class="{cls}">{h(fmt_local(lot.deadline_at))} · {h(remaining_uz(lot.deadline_at, now))}</span>'


def _verdict_badge(lot: Lot) -> str:
    a = lot.analysis or {}
    if not a:
        return '<span class="badge">⏳ tahlil kutilmoqda</span>'
    if a.get("error"):
        return '<span class="badge">⚠️ baholanmagan</span>'
    v = lot.verdict or "?"
    fit = f" · fit {h(a.get('fit_score'))}" if a.get("fit_score") is not None else ""
    return f'<span class="badge {VERDICT_CLASS.get(v, "")}">{h(tc.VERDICT_BADGE.get(v, v))}{fit}</span>'


def _price(lot: Lot) -> str:
    cost_usd = tc.usd(lot.start_cost, lot.currency, lot.usd_rate)
    usd = f" ({tc.fmt_usd(cost_usd)})" if cost_usd is not None else ""
    return h(tc.money_uzs(lot.start_cost, lot.currency) + usd)


def lot_actions(ctx: Ctx, lot: Lot) -> str:
    if not ctx.admin:
        return ""
    out = []
    if lot.status == LotStatus.BIDDING:
        out.append(_action(ctx, f"/lot/{lot.id}/unbid", "↩️ Qatnashmaymiz", "ghost"))
    else:
        out.append(_action(ctx, f"/lot/{lot.id}/bid", "📌 Qatnashamiz"))
    if lot.status != LotStatus.IGNORED:
        out.append(_action(ctx, f"/lot/{lot.id}/ignore", "🚫 Kerak emas", "ghost"))
    return "".join(out)


def lot_item(ctx: Ctx, lot: Lot, now: datetime) -> str:
    a = lot.analysis or {}
    summary = a.get("summary_uz") or lot.title
    status = tc.STATUS_UZ.get(lot.status, lot.status)
    return f"""<article class="item">
<div class="top">{_verdict_badge(lot)}<span class="chip">{h(tc.SOURCE_NAME.get(lot.source, lot.source))}</span>
<span class="lotno">№ {ext_link(lot.url, lot.external_id)}</span><span>{h(status)}</span></div>
<a class="title" href="{h(ctx.url(f"/lot/{lot.id}"))}">{h(tc.short(summary, 220))}</a>
<div class="meta"><span>🏢 {h(tc.short(lot.customer_name, 90) or tc.UNKNOWN_CUSTOMER)}</span>
<span>💰 <b>{_price(lot)}</b></span><span>⏰ {_deadline(lot, now)}</span></div>
<div class="actions">{ext_link(lot.url, "🔗 Saytda ochish", "btn")}
<a class="btn ghost" href="{h(ctx.url(f"/lot/{lot.id}"))}">📄 Batafsil</a>{lot_actions(ctx, lot)}</div>
</article>"""


def _sort_inputs(sort: str, direction: str) -> str:
    return (f'<input type="hidden" name="sort" value="{h(sort)}"><input type="hidden" name="dir" value="{h(direction)}">')


def tenders_page(ctx: Ctx, lots: list[Lot], *, q: str, verdict: str, status: str, counts: dict[str, int],
                 health: dict[str, dict], sort: str = "deadline", direction: str = "asc") -> str:
    from app.web.tables import LOT_COLS, table_html

    stats = "".join(f'<div class="stat"><b>{n}</b><span>{h(label)}</span></div>' for label, n in [
        ("ochiq IT lot", counts.get("open", 0)), ("✅ qatnashsa bo'ladi", counts.get("good", 0)),
        ("🟡 shartli", counts.get("maybe", 0)), ("📌 qatnashyapmiz", counts.get("bidding", 0)),
    ])

    def opt(value: str, current: str, label: str) -> str:
        return f'<option value="{h(value)}"{" selected" if value == current else ""}>{h(label)}</option>'

    filters = f"""<form class="filters" method="get" action="{h(ctx.url("/lots"))}">
<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi, buyurtmachi, lot raqami…">
<select name="v">{opt("", verdict, "Barcha xulosalar")}{opt("good", verdict, "✅ Qatnashamiz")}
{opt("maybe", verdict, "🟡 Shartli")}{opt("bad", verdict, "❌ Yo'q")}</select>
<select name="s">{opt("open", status, "Ochiqlar")}{opt("bidding", status, "📌 Qatnashayotganlar")}
{opt("all", status, "Hammasi (yopilganlar bilan)")}</select>{_sort_inputs(sort, direction)}
<button type="submit">Ko'rsatish</button>
<a class="btn ghost" href="{h(ctx.url("/lots.xlsx", q=q, v=verdict, s=status, sort=sort, dir=direction))}">⬇️ Excel</a>
</form>"""
    down = [n for n, v in health.items() if v.get("alerted")]
    health_html = (f'<p class="sub">⚠️ Hozir ishlamayapti: {h(", ".join(down))}</p>' if down else "")
    table = table_html(ctx, "/lots", lots, LOT_COLS, sort=sort, direction=direction,
                       query={"q": q, "v": verdict, "s": status}, detail=lambda lot: ctx.url(f"/lot/{lot.id}"),
                       bulk=[("bid", "📌 Qatnashamiz", ""), ("ignore", "🚫 Kerak emas", "ghost"),
                             ("unbid", "↩️ Qatnashmaymiz", "ghost")], noun="lot")
    return layout(ctx, "tenders", f"""<h1>🏛 Davlat IT tenderlari</h1>
<p class="sub">etender.uzex.uz va xarid.uzex.uz dagi IT lotlar, AI tahlili bilan. Ustun nomini bosing — saralanadi,
qatorni bosing — to'liq ma'lumot va AI xulosasi ochiladi.</p>{health_html}
<div class="stats">{stats}</div>{filters}{table}""")


def lot_page(ctx: Ctx, lot: Lot, *, used_usd: float, limit_usd: float) -> str:
    now = now_utc()
    a = lot.analysis or {}
    rows: list[tuple[str, str]] = [
        ("Lot raqami", f'<span class="lotno">{ext_link(lot.url, lot.external_id)}</span>'),
        ("Manba", h(f"{tc.SOURCE_NAME.get(lot.source, lot.source)} · {lot.lot_type or '?'}")),
        ("Buyurtmachi", h(lot.customer_name or tc.UNKNOWN_CUSTOMER)),
        ("INN / viloyat", h(f"{lot.customer_tin or '?'} · {lot.customer_region or '?'}")),
        ("Boshlang'ich narx", f"<b>{_price(lot)}</b>"),
        ("Zakalat", h(tc.pledge_line(lot, used_usd, limit_usd))),
        ("E'lon qilingan", h(fmt_local(lot.published_at))),
        ("Topshirish muddati", _deadline(lot, now)),
        ("Holat", h(f"{tc.STATUS_UZ.get(lot.status, lot.status)} · saytda: {lot.platform_status or '?'}")),
    ]
    if lot.price_weight is not None and lot.tech_weight is not None:
        rows.append(("Baholash", h(f"Narx {lot.price_weight:g}% / texnik {lot.tech_weight:g}%"
                                   + (f" · o'tish bali {lot.pass_mark:g}" if lot.pass_mark is not None else ""))))
    elif lot.valuation_method:
        rows.append(("Baholash", h(lot.valuation_method)))
    terms = [
        f"avans {lot.advance_percent:g}%" if lot.advance_percent is not None else None,
        f"to'lov {lot.payment_days} kun" if lot.payment_days else None,
        f"ish muddati {lot.execution_days} kun" if lot.execution_days else None,
        f"kafolat {lot.warranty}" if lot.warranty else None,
    ]
    rows.append(("Shartlar", h(", ".join(t for t in terms if t) or "noma'lum")))
    rows.append(("Til / moliya", h(f"{', '.join(lot.languages) or '?'} · {lot.financing_source or '?'}")))
    if lot.matched_keywords:
        rows.append(("Kalit so'zlar", h(", ".join(lot.matched_keywords[:10]))))
    kv = "".join(f"<dt>{h(k)}</dt><dd>{v}</dd>" for k, v in rows)

    def bullets(title: str, items, limit: int = 12) -> str:
        items = [x for x in (items or []) if x]
        if not items:
            return ""
        return f"<h2>{h(title)}</h2><div class=\"panel\"><ul>" + "".join(
            f"<li>{h(x)}</li>" for x in items[:limit]) + "</ul></div>"

    ai = ""
    if a and not a.get("error"):
        reason = f"<p>{h(a['verdict_reason_uz'])}</p>" if a.get("verdict_reason_uz") else ""
        extra = []
        tech = a.get("estimated_tech_score") or []
        if len(tech) == 2:
            extra.append(f"Taxminiy texnik ball: {tech[0]:g}–{tech[1]:g}")
        win = a.get("win_chance_percent") or []
        if len(win) == 2:
            extra.append(f"Yutish ehtimoli: {win[0]}–{win[1]}%")
        if a.get("hardware_or_server_required"):
            extra.append(f"Server/uskuna: {a['hardware_or_server_required']}")
        ai = (f'<h2>🤖 AI xulosasi {_verdict_badge(lot)}</h2><div class="panel">{reason}'
              + ("<ul>" + "".join(f"<li>{h(x)}</li>" for x in extra) + "</ul>" if extra else "") + "</div>")
        ai += bullets("⛔ To'siqlar", a.get("blockers"))
        ai += bullets("⚠️ Risklar", a.get("risks"))
        ai += bullets("↔️ Qarama-qarshiliklar", a.get("contradictions"))
        ai += bullets("❓ Buyurtmachiga savollar", a.get("questions_for_customer_uz"))
    elif a.get("error"):
        ai = '<div class="panel muted">⚠️ AI bu lotni hali baholay olmadi.</div>'

    criteria = ""
    if lot.criteria:
        criteria = "<h2>📋 Texnik mezonlar</h2><div class=\"panel\"><ul>" + "".join(
            f"<li>{h(c.get('label'))}"
            + (f" — <b>{h(format(c['max_score'], 'g'))} ball</b>" if c.get("max_score") is not None else "")
            + "</li>" for c in lot.criteria[:20]) + "</ul></div>"
    files = ""
    if lot.files:
        files = "<h2>📎 Fayllar (asl saytda)</h2><div class=\"panel\"><ul>" + "".join(
            f"<li>{h(f.get('label'))}</li>" for f in lot.files) + "</ul></div>"
    desc = ""
    if lot.description:
        desc = f'<h2>📝 Tavsif</h2><div class="panel prose">{h(tc.short(lot.description, 4000))}</div>'
    body = f"""<p><a href="{h(ctx.url("/lots"))}">← Barcha lotlar</a></p>
<h1>{h(lot.title)}</h1>
<p class="sub">{h(a.get("summary_uz") or "")}</p>
<div class="actions" style="display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px">
{ext_link(lot.url, "🔗 Asl e'lonni saytda ochish", "btn open-site")}{lot_actions(ctx, lot)}</div>
<div class="panel"><dl class="kv">{kv}</dl></div>{ai}{criteria}
{bullets("✔️ Malaka talablari", lot.qualification)}{files}{desc}"""
    return layout(ctx, "tenders", body, title=f"Lot {lot.external_id}")


# ── Freelance jobs ──────────────────────────────────────────────────────────
def _budget(job: Job) -> str:
    if job.budget_max is None and job.budget_min is None:
        return "ko'rsatilmagan"
    if job.budget_min and job.budget_max and job.budget_min != job.budget_max:
        txt = f"{money(job.budget_min, job.currency)}–{money(job.budget_max, job.currency)}"
    else:
        txt = money(job.budget_max or job.budget_min, job.currency)
    if job.budget_type == "hourly":
        txt += "/soat"
    if job.budget_usd and (job.currency or "USD").upper() != "USD":
        txt += f" (≈ ${job.budget_usd:,.0f})"
    return txt


def _fit_badge(job: Job) -> str:
    if job.fit is None:
        return '<span class="badge">baholanmagan</span>'
    cls = "good" if job.fit >= 80 else "maybe" if job.fit >= 60 else "bad"
    win = f" · yutish {job.win_probability}%" if job.win_probability is not None else ""
    return f'<span class="badge {cls}">fit {job.fit}{h(win)}</span>'


def job_actions(ctx: Ctx, job: Job) -> str:
    if not ctx.admin:
        return ""
    out = []
    if job.status == JobStatus.AWAITING_APPROVAL:
        out.append(_action(ctx, f"/job/{job.id}/approve", "✅ Taklifni tasdiqlash"))
    if job.status in (JobStatus.AWAITING_APPROVAL, JobStatus.SCORED, JobStatus.DRAFTED, JobStatus.FOUND):
        out.append(_action(ctx, f"/job/{job.id}/skip", "⏭ O'tkazib yuborish", "ghost"))
    return "".join(out)


def job_item(ctx: Ctx, job: Job, now: datetime) -> str:
    status = STATUS_LABEL_UZ.get(job.status, job.status)
    extra = f"<span>📨 {job.proposals_count} taklif</span>" if job.proposals_count is not None else ""
    return f"""<article class="item">
<div class="top">{_fit_badge(job)}<span class="chip">{h(PLATFORM_NAME.get(job.platform, job.platform))}</span>
<span>{h(status)}</span><span>{h(ago_uz(job.posted_at or job.created_at, now))}</span></div>
<a class="title" href="{h(ctx.url(f"/job/{job.id}"))}">{h(tc.short(job.title, 200))}</a>
<div class="meta"><span>💰 <b>{h(_budget(job))}</b></span>{extra}</div>
{f'<p class="muted" style="margin:6px 0 0">{h(tc.short(job.summary_uz, 260))}</p>' if job.summary_uz else ""}
<div class="actions">{ext_link(job.url, "🔗 Saytda ochish", "btn")}
<a class="btn ghost" href="{h(ctx.url(f"/job/{job.id}"))}">📄 Batafsil</a>{job_actions(ctx, job)}</div>
</article>"""


def jobs_page(ctx: Ctx, jobs: list[Job], *, q: str, platform: str, show: str, platforms: list[str],
              counts: dict[str, int], sort: str = "posted", direction: str = "desc") -> str:
    from app.web.tables import JOB_COLS, table_html


    def opt(value: str, current: str, label: str) -> str:
        return f'<option value="{h(value)}"{" selected" if value == current else ""}>{h(label)}</option>'

    stats = "".join(f'<div class="stat"><b>{n}</b><span>{h(label)}</span></div>' for label, n in [
        ("mos ish (AI tavsiya)", counts.get("active", 0)), ("⏳ tasdiq kutmoqda", counts.get("awaiting", 0)),
        ("📤 yuborilgan", counts.get("submitted", 0)), ("🎉 olingan ish", counts.get("hired", 0)),
    ])
    plat_opts = opt("", platform, "Barcha platformalar") + "".join(
        opt(p, platform, PLATFORM_NAME.get(p, p)) for p in platforms)
    filters = f"""<form class="filters" method="get" action="{h(ctx.url("/jobs"))}">
<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi yoki tavsifi…">
<select name="p">{plat_opts}</select>
<select name="show">{opt("good", show, "AI tavsiya qilganlar")}{opt("all", show, "Hammasi (o'tkazilganlar bilan)")}
</select>{_sort_inputs(sort, direction)}<button type="submit">Ko'rsatish</button>
<a class="btn ghost" href="{h(ctx.url("/jobs.xlsx", q=q, p=platform, show=show, sort=sort, dir=direction))}">⬇️ Excel</a>
</form>"""
    table = table_html(ctx, "/jobs", jobs, JOB_COLS, sort=sort, direction=direction,
                       query={"q": q, "p": platform, "show": show}, detail=lambda j: ctx.url(f"/job/{j.id}"),
                       bulk=[("approve", "✅ Tasdiqlash", ""), ("skip", "⏭ O'tkazib yuborish", "ghost")],
                       noun="ish")
    return layout(ctx, "jobs", f"""<h1>💼 Frilans ishlar</h1>
<p class="sub">FL.ru, Kwork, Freelancer.com va Upwork/Fiverr (Gmail) dan topilgan IT ishlar. Ustun nomini bosing —
saralanadi, qatorni bosing — to'liq ochiladi.</p>
<div class="stats">{stats}</div>{filters}{table}""")


def job_page(ctx: Ctx, job: Job, proposal: Proposal | None) -> str:
    now = now_utc()
    rows = [
        ("Platforma", h(PLATFORM_NAME.get(job.platform, job.platform))),
        ("Byudjet", f"<b>{h(_budget(job))}</b>"),
        ("E'lon qilingan", h(f"{fmt_local(job.posted_at)} ({ago_uz(job.posted_at, now)})" if job.posted_at
                             else "noma'lum")),
        ("Holat", h(STATUS_LABEL_UZ.get(job.status, job.status))),
    ]
    if job.proposals_count is not None:
        rows.append(("Takliflar soni", h(job.proposals_count)))
    if job.skills:
        rows.append(("Ko'nikmalar", h(", ".join(job.skills[:15]))))
    client = [x for x in [
        job.client_country,
        f"reyting {job.client_rating:g}" if job.client_rating is not None else None,
        f"sarflagan ${job.client_total_spent_usd:,.0f}" if job.client_total_spent_usd is not None else None,
        "to'lov tasdiqlangan" if job.client_payment_verified else None,
    ] if x]
    if client:
        rows.append(("Mijoz", h(", ".join(client))))
    if job.effort_hours:
        rows.append(("Taxminiy hajm", h(f"{job.effort_hours:g} soat")))
    if job.risk_flags:
        rows.append(("Risklar", h(", ".join(RISK_UZ.get(r, r) for r in job.risk_flags))))
    kv = "".join(f"<dt>{h(k)}</dt><dd>{v}</dd>" for k, v in rows)
    summary = f'<h2>🤖 AI xulosasi {_fit_badge(job)}</h2><div class="panel">{h(job.summary_uz)}</div>' \
        if job.summary_uz else ""
    prop = ""
    if proposal is not None and ctx.admin:
        prop = (f'<h2>✍️ Taklif matni (v{proposal.version}, {h(money(proposal.price, proposal.currency))}'
                f'{f", {proposal.timeline_days} kun" if proposal.timeline_days else ""})</h2>'
                f'<div class="panel prose">{h(proposal.text)}</div>')
    desc = f'<h2>📝 Tavsif</h2><div class="panel prose">{h(tc.short(job.description, 6000))}</div>' \
        if job.description else ""
    body = f"""<p><a href="{h(ctx.url("/jobs"))}">← Barcha ishlar</a></p><h1>{h(job.title)}</h1>
<div class="actions" style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 14px">
{ext_link(job.url, "🔗 Asl e'lonni saytda ochish", "btn open-site")}{job_actions(ctx, job)}</div>
<div class="panel"><dl class="kv">{kv}</dl></div>{summary}{prop}{desc}"""
    return layout(ctx, "jobs", body, title=job.title)


def login_page(ctx: Ctx, *, error: str = "", next_url: str = "", user: str = "") -> str:
    if not ctx.login_enabled:
        body = '<div class="panel login">Sayt yopiq: serverda WEB_ADMIN_PASSWORD sozlanmagan.</div>'
        return layout(ctx, "", body, title="Kirish")
    err = f'<div class="flash err">{h(error)}</div>' if error else ""
    body = f"""<form class="panel login" method="post" action="{h(ctx.url("/login"))}">
<h1>🔐 Kirish</h1><p class="sub">Telegram xabarlari, tenderlar va frilans ishlarni ko'rish uchun kiring.</p>{err}
<input type="hidden" name="next" value="{h(next_url)}">
<label>Login<input type="text" name="user" value="{h(user)}" autocomplete="username" autocapitalize="none"
required {"" if user else "autofocus"}></label>
<label>Parol<input type="password" name="password" autocomplete="current-password" required
{"autofocus" if user else ""}></label>
<button type="submit">Kirish</button></form>"""
    return layout(ctx, "", body, title="Kirish")


# ── Telegram feed ───────────────────────────────────────────────────────────
KIND_LABEL = {"tender": "🏛 Tender", "freelance": "💼 Frilans", "email": "📧 Gmail", "status": "🔄 Holat"}


def _msg_buttons(ctx: Ctx, rows: list[list[dict]]) -> str:
    links: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        for b in row:
            url, cb = b.get("url"), b.get("cb") or ""
            if url and safe_url(url):
                href, text, external = url, b.get("text") or "🔗", True
            else:
                parts = cb.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    continue
                if parts[0] == "tnd":
                    href, text, external = ctx.url(f"/lot/{parts[2]}"), "📄 Batafsil", False
                elif parts[0] == "job":
                    href, text, external = ctx.url(f"/job/{parts[2]}"), "📄 Batafsil", False
                else:
                    continue
            if href in seen:
                continue
            seen.add(href)
            links.append(ext_link(href, text) if external else f'<a href="{h(href)}">{h(text)}</a>')
    return f'<div class="kb">{"".join(links)}</div>' if links else ""


def feed_page(ctx: Ctx, messages, *, kind: str, kinds: list[str], next_before: int | None) -> str:
    from app.web.tghtml import to_web

    tabs = [("", "Hammasi")] + [(k, KIND_LABEL.get(k, k)) for k in kinds]
    tab_html = "".join(f'<a class="{"on" if k == kind else ""}" href="{h(ctx.url("/", k=k))}">{h(label)}</a>'
                       for k, label in tabs) if len(kinds) > 1 else ""
    items = "".join(f"""<article class="msg"><div class="body">{to_web(m.text)}</div>{_msg_buttons(ctx, m.buttons)}
<div class="when"><span>{h(KIND_LABEL.get(m.kind, m.kind))}</span><span>{h(fmt_local(m.created_at))}</span></div>
</article>""" for m in messages) or '<div class="empty">Hozircha xabar yo\'q.</div>'
    more = (f'<p><a class="btn ghost" href="{h(ctx.url("/", k=kind, before=next_before))}">↓ Eskiroq xabarlar</a></p>'
            if next_before else "")
    note = ""
    body = f"""<h1>📨 Telegram xabarlari</h1>
<p class="sub">Bot Telegram'ga yuborgan xabarlarning o'zi, eng yangisi tepada. Sahifa har 2 daqiqada yangilanadi.</p>
{note}<div class="tabs">{tab_html}</div><div class="feed">{items}</div>{more}"""
    return layout(ctx, "feed", body, title="Telegram xabarlari").replace(
        "<title>", '<meta http-equiv="refresh" content="120"><title>', 1)
