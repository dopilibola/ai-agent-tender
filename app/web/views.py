"""HTML rendering for the dashboard (Uzbek). Everything from the DB is untrusted → always escaped via h()."""

from __future__ import annotations

import re
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
                 here: str = "", flash: str = "", theme: str = "auto"):
        self.base, self.title = base, title
        self.admin = admin_exp is not None
        self.csrf = csrf
        self.login_enabled = login_enabled
        self.here = here    # current path+query: where an action returns to
        self.flash = flash  # one-shot message after an action
        self.theme = theme if theme in THEMES else "auto"

    def url(self, path: str = "/", **query) -> str:
        q = {k: v for k, v in query.items() if v not in (None, "")}
        return f"{self.base}{path}" + (f"?{urlencode(q)}" if q else "")


CSS = """
:root{color-scheme:light;--hover:#efefef;--active:#e8e8e8;--topbg:rgba(255,255,255,.92);--field:#fff;--btn-hover:#333;--on-ink:#fff;--seg-on:#fff;--good-line:#bbf7d0;--bad-line:#fecaca;--bg:#fff;--side:#fafafa;--card:#fff;--soft:#fafafa;--ink:#171717;--muted:#737373;
--faint:#a3a3a3;--line:#e5e5e5;--line2:#d4d4d4;--brand:#2563eb;--brand2:#1d4ed8;--brand-bg:#eff6ff;
--good:#15803d;--good-bg:#f0fdf4;--maybe:#a16207;--maybe-bg:#fefce8;--bad:#b91c1c;--bad-bg:#fef2f2;--chip:#f5f5f5;
--mono:ui-monospace,"SF Mono","IBM Plex Mono",Menlo,Consolas,monospace}
:root[data-theme=dark]{color-scheme:dark;--hover:#222;--active:#2a2a2a;--topbg:rgba(10,10,10,.88);--field:#141414;--btn-hover:#d4d4d4;--on-ink:#0a0a0a;--seg-on:#2a2a2a;--good-line:#14532d;--bad-line:#7f1d1d;
--bg:#0a0a0a;--side:#111;--card:#141414;--soft:#1a1a1a;--ink:#ededed;--muted:#a1a1a1;--faint:#6b6b6b;--line:#262626;--line2:#3a3a3a;--brand:#60a5fa;--brand2:#3b82f6;--brand-bg:#172554;
--good:#4ade80;--good-bg:#052e16;--maybe:#facc15;--maybe-bg:#2a2205;--bad:#f87171;--bad-bg:#2c0b0b;--chip:#1f1f1f}
.theme .sun,[data-theme=dark] .theme .moon{display:none}[data-theme=dark] .theme .sun{display:block}
aside form{margin:0}
.themes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;max-width:520px}
.themes button{height:auto;flex-direction:column;gap:8px;padding:12px;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:12px}
.themes button:hover{background:var(--soft)}.themes button.on{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-bg)}
.themes .sw{width:100%;height:44px;border-radius:8px;border:1px solid var(--line2)}
.sw.light{background:linear-gradient(90deg,#fafafa 30%,#fff 30%)}.sw.dark{background:linear-gradient(90deg,#111 30%,#0a0a0a 30%)}
.sw.auto{background:linear-gradient(135deg,#fff 50%,#0a0a0a 50%)}
.avatar{width:56px;height:56px;border-radius:14px;background:var(--ink);color:var(--on-ink);display:grid;place-content:center;font-size:22px;font-weight:700}
.prof{display:flex;gap:16px;align-items:center}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 "IBM Plex Sans Variable","IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
svg.i{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;
stroke-linejoin:round}
.app{display:grid;grid-template-columns:56px minmax(0,1fr);min-height:100vh}.app>div{min-width:0}
aside{background:var(--side);border-right:1px solid var(--line);padding:12px 0;position:sticky;top:0;height:100vh;
display:flex;flex-direction:column;align-items:center;gap:6px;z-index:5}
.logo{width:28px;height:28px;border-radius:8px;background:var(--ink);color:var(--on-ink);display:grid;place-content:center;
font-weight:700;font-size:13px;margin-bottom:10px}.logo:hover{text-decoration:none}
aside nav{display:flex;flex-direction:column;gap:4px}
aside nav a,aside .out{position:relative;width:36px;height:36px;border-radius:8px;display:grid;place-content:center;
color:var(--muted);background:none;border:0;padding:0;cursor:pointer}
aside nav a:hover,aside .out:hover{background:var(--hover);color:var(--ink);text-decoration:none}
aside nav a.on{background:var(--active);color:var(--ink)}
aside [data-tip]:hover::after{content:attr(data-tip);position:absolute;left:44px;top:50%;transform:translateY(-50%);
background:var(--ink);color:var(--on-ink);font-size:12px;font-weight:500;padding:4px 8px;border-radius:6px;white-space:nowrap;
pointer-events:none}
aside .grow{flex:1}
.top{height:49px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;padding:0 24px;
position:sticky;top:0;background:var(--topbg);backdrop-filter:blur(6px);z-index:4}
.top b{font-size:15px;font-weight:600;white-space:nowrap}.top .sub{color:var(--muted);font-size:13px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}.top .tools{margin-left:auto;display:flex;gap:8px;align-items:center}
main{padding:20px 24px 48px;min-width:0}
h1{font-size:20px;margin:0 0 6px;font-weight:600;letter-spacing:-.01em}
h2{font-size:15px;margin:22px 0 8px;font-weight:600;display:flex;gap:8px;align-items:center}
p.sub{color:var(--muted);margin:0 0 16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:0 0 16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat span{color:var(--muted);font-size:13px;display:block}
.stat b{display:block;font:500 24px/1.3 var(--mono);margin:6px 0 2px;letter-spacing:-.02em}
.stat b.blue{color:var(--brand)}.stat small{color:var(--muted);font-size:12px}
form.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;align-items:center}
form.filters .grp{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:13px}
input,select,button,textarea{font:inherit;color:inherit}
input[type=text],input[type=search],input[type=password],input[type=number],select{background:var(--field);
border:1px solid var(--line);border-radius:8px;padding:0 10px;height:34px;outline:none;font-size:13px}
input[type=number]{width:96px;font-family:var(--mono)}input[type=number].sm{width:70px}
input:focus,select:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-bg)}
input[type=search]{flex:1;min-width:220px}
button,.btn{background:var(--ink);color:var(--on-ink);border:1px solid var(--ink);border-radius:8px;padding:0 13px;height:34px;
font-weight:500;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
button:hover,.btn:hover{background:var(--btn-hover);text-decoration:none}
.btn.ghost,button.ghost{background:var(--field);color:var(--ink);border-color:var(--line)}
.btn.ghost:hover,button.ghost:hover{background:var(--soft)}
.btn.link{background:none;border-color:transparent;color:var(--muted)}.btn.link:hover{color:var(--ink);background:none}
.seg{display:inline-flex;background:var(--chip);border-radius:9px;padding:3px;gap:2px}
.seg a{padding:5px 11px;border-radius:7px;color:var(--muted);font-size:13px;font-weight:500}
.seg a:hover{color:var(--ink);text-decoration:none}.seg a.on{background:var(--seg-on);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.badge,.pill{display:inline-flex;align-items:center;gap:5px;border-radius:6px;padding:2px 8px;font-weight:500;
font-size:12px;white-space:nowrap;background:var(--chip);color:var(--muted);border:1px solid transparent}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.8}
.badge.good,.pill.good{background:var(--good-bg);color:var(--good)}
.badge.maybe,.pill.maybe{background:var(--maybe-bg);color:var(--maybe)}
.badge.bad,.pill.bad,.pill.solid-bad{background:var(--bad-bg);color:var(--bad)}
.pill.blue{background:var(--brand-bg);color:var(--brand)}
.chip{background:var(--chip);border-radius:6px;padding:1px 7px;font-size:12px;color:var(--muted)}
.tag{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 7px}
.lotno{font-family:var(--mono);font-size:13px}
.urgent{color:var(--bad)!important;font-weight:600}.soon{color:var(--maybe)!important;font-weight:600}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 12px}
dl.kv{display:grid;grid-template-columns:minmax(120px,200px) 1fr;gap:9px 16px;margin:0}
dl.kv dt{color:var(--muted)}dl.kv dd{margin:0}
ul{margin:4px 0;padding-left:20px}li{margin:3px 0}
.prose{white-space:pre-wrap;word-wrap:break-word}
.muted{color:var(--muted)}.empty{padding:48px;text-align:center;color:var(--muted)}
.flash{background:var(--good-bg);color:var(--good);border:1px solid var(--good-line);border-radius:10px;padding:10px 14px;
margin:0 0 14px;font-weight:500}
.flash.err{background:var(--bad-bg);color:var(--bad);border-color:var(--bad-line)}
footer{padding:0 24px 24px;color:var(--faint);font-size:12px}
.count{color:var(--muted);margin:0 0 8px;font-size:13px}
.bulk{display:none;gap:8px;align-items:center;margin:0 0 10px;padding:6px 8px 6px 14px;background:var(--soft);
border:1px solid var(--line);border-radius:10px}.bulk.show{display:flex}
.tablewrap{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;max-height:calc(100vh - 120px)}
table.grid{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}
.grid th,.grid td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;
vertical-align:middle}
.grid tbody tr:last-child td{border-bottom:0}
.grid thead th{position:sticky;top:0;background:var(--soft);font-weight:500;color:var(--muted);z-index:2;font-size:12px}
.grid th a{color:var(--muted);display:flex;align-items:center;justify-content:space-between;gap:8px}
.grid th a:hover{text-decoration:none;color:var(--ink)}
.grid th .si{color:var(--faint);flex:none}.grid th.on .si,.grid th.on a{color:var(--ink)}
.grid tbody tr{cursor:pointer}.grid tbody tr:hover td{background:var(--soft)}
.grid tbody tr.sel td{background:var(--brand-bg)}
.grid td.num,.grid th.num{text-align:right}.grid th.num a{justify-content:flex-end}
.grid td.num{font-family:var(--mono);font-size:12.5px}
.grid td.wide{max-width:380px;overflow:hidden;text-overflow:ellipsis}
.grid td.mid{max-width:220px;overflow:hidden;text-overflow:ellipsis}
.grid td.dim{color:var(--muted);font-family:var(--mono);font-size:12px}
.grid tfoot td{background:var(--soft);font-weight:600;border-top:1px solid var(--line);border-bottom:0;position:sticky;bottom:0}
.grid .cb{width:40px;padding-right:0}
.grid input[type=checkbox]{appearance:none;width:16px;height:16px;border:1.5px solid var(--line2);border-radius:4px;
background:var(--field);cursor:pointer;vertical-align:middle;margin:0;display:inline-grid;place-content:center}
.grid input[type=checkbox]:checked{background:var(--ink);border-color:var(--ink)}
.grid input[type=checkbox]:checked::after{content:"";width:8px;height:4px;border:2px solid var(--on-ink);border-top:0;
border-right:0;transform:rotate(-45deg) translate(1px,-1px)}
.bar{display:inline-flex;align-items:center;gap:8px}.bar i{display:inline-block;width:44px;height:5px;border-radius:3px;
background:var(--chip);overflow:hidden}.bar i b{display:block;height:100%;background:var(--brand);border-radius:3px}
.nowrap{white-space:nowrap}
.feed{display:grid;gap:10px;max-width:760px}
.msg{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px 10px}
.msg .body{white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
.msg .body pre,.msg .body code{font-family:var(--mono);font-size:12.5px;background:var(--chip);border-radius:5px;padding:0 4px}
.msg .body blockquote{margin:4px 0;padding-left:10px;border-left:3px solid var(--line2);color:var(--muted)}
.msg .when{display:flex;justify-content:space-between;gap:8px;color:var(--faint);font-size:12px;margin-top:8px}
.kb{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.kb a{flex:1 1 auto;text-align:center;background:var(--soft);border:1px solid var(--line);border-radius:8px;
padding:5px 10px;font-weight:500;font-size:13px;color:var(--ink)}
.spoiler{background:var(--muted);color:transparent;border-radius:3px}.spoiler:hover{color:inherit;background:none}
.tabs{margin:0 0 14px}
.login{max-width:360px;margin:14vh auto 0;display:grid;gap:12px;padding:28px}
.login label{display:grid;gap:5px;color:var(--muted);font-size:13px}.login button{height:38px;justify-content:center}
.login input{height:38px!important}
.solo main{max-width:none}
@media (max-width:760px){.app{grid-template-columns:1fr}aside{position:sticky;height:auto;flex-direction:row;
padding:8px 12px;border-right:0;border-bottom:1px solid var(--line)}.logo{margin:0 8px 0 0}aside nav{flex-direction:row}
aside [data-tip]:hover::after{display:none}.top{position:static;padding:0 16px}.top .sub{display:none}
main{padding:14px}dl.kv{grid-template-columns:1fr}input[type=search]{min-width:100%}}
"""
# "Avtomatik" follows the device: same dark palette, applied by the OS setting.
_DARK = re.search(r":root\[data-theme=dark\]\{([^}]*)\}", CSS).group(1)
CSS += ("@media (prefers-color-scheme:dark){:root[data-theme=auto]{" + _DARK + "}"
        "[data-theme=auto] .theme .moon{display:none}[data-theme=auto] .theme .sun{display:block}}")
THEMES = {"light": "Oq", "dark": "Qora", "auto": "Avtomatik"}
ASSET_VER = "1"  # set by server.py from the app.js hash so Cloudflare/browser caches never serve a stale file

_ICON = {
    "orders": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 15h18M9 9.5V20"/>',
    "tenders": '<path d="M3 21h18M5 21v-9M10 21v-9M14 21v-9M19 21v-9M2.5 9.5 12 4l9.5 5.5z"/>',
    "goods": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/>',
    "jobs": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5.5A1.5 1.5 0 0 1 9.5 4h5A1.5 1.5 0 0 1 16 5.5V7M3 12.5h18"/>',
    "feed": '<path d="M21 4 3 11l7 2.5M21 4l-4 16-7-6.5M21 4 10 13.5V19l3-3"/>',
    "logout": '<path d="M9 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h3M15 16l4-4-4-4M19 12H9"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5"/>',
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "ext": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
}


def icon(name: str, cls: str = "") -> str:
    return f'<svg class="i {cls}" viewBox="0 0 24 24" aria-hidden="true">{_ICON[name]}</svg>'


def layout(ctx: Ctx, active: str, body: str, *, title: str = "", flash: str = "", error: bool = False,
           sub: str = "", tools: str = "") -> str:
    tabs = [("orders", ctx.url("/"), "orders", "Zakazlar"), ("tenders", ctx.url("/lots"), "tenders", "Tenderlar"),
            ("goods", ctx.url("/all"), "goods", "Barcha tenderlar (tovar)"),
            ("jobs", ctx.url("/jobs"), "jobs", "Frilans ishlar"), ("feed", ctx.url("/feed"), "feed", "Telegram")]
    flash = flash or ctx.flash
    flash_html = f'<div class="flash{" err" if error else ""}">{h(flash)}</div>' if flash else ""
    page_title = f"{title} — {ctx.title}" if title else ctx.title
    head = f"""<!DOCTYPE html>
<html lang="uz" data-theme="{h(ctx.theme)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{h(page_title)}</title><style>{CSS}</style>
<script src="{h(ctx.url("/static/app.js", v=ASSET_VER))}" defer></script></head>"""
    if not ctx.admin:  # login screen: nothing but the form
        return f'{head}<body class="solo"><main>{flash_html}{body}</main></body></html>'
    nav = "".join(f'<a class="{"on" if key == active else ""}" href="{h(url)}" data-tip="{h(label)}" '
                  f'aria-label="{h(label)}">{icon(ic)}</a>' for key, url, ic, label in tabs)
    logout = (f'<form method="post" action="{h(ctx.url("/logout"))}">'
              f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
              f'<button class="out" type="submit" data-tip="Chiqish" aria-label="Chiqish">{icon("logout")}</button></form>')
    theme_btn = (f'<form method="post" action="{h(ctx.url("/theme"))}">'
                 f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
                 f'<input type="hidden" name="back" value="{h(ctx.here)}">'
                 f'<input type="hidden" name="cur" value="{h(ctx.theme)}">'
                 f'<button class="out theme" type="submit" name="theme" value="toggle" data-tip="Qora / oq rejim" '
                 f'aria-label="Qora / oq rejim">{icon("moon", "moon")}{icon("sun", "sun")}</button></form>')
    profile = (f'<a class="out{" on" if active == "profile" else ""}" href="{h(ctx.url("/profile"))}" '
               f'data-tip="Profil" aria-label="Profil">{icon("user")}</a>')
    initial = h((ctx.title or "A")[:1].upper())
    top = (f'<div class="top"><b>{h(title or ctx.title)}</b><span class="sub">{h(sub)}</span>'
           f'<div class="tools">{tools}</div></div>')
    return f"""{head}<body><div class="app">
<aside><a class="logo" href="{h(ctx.url("/"))}" title="{h(ctx.title)}">{initial}</a>
<nav>{nav}</nav><div class="grow"></div>{theme_btn}{profile}{logout}</aside>
<div>{top}<main>{flash_html}{body}</main>
<footer>Tenderlar har 30 daqiqada, frilans ishlar har 5–10 daqiqada yangilanadi · Toshkent vaqti ·
{h(fmt_local(now_utc()))}</footer></div></div></body></html>"""


def filter_bar(ctx: Ctx, path: str, inner: str, *, xlsx: str = "") -> str:
    """Filter row (GET form) + reset link + optional Excel download."""
    dl = f'<a class="btn ghost" href="{h(xlsx)}">{icon("download")}Excel</a>' if xlsx else ""
    return (f'<form class="filters" method="get" action="{h(ctx.url(path))}">{inner}'
            f'<button type="submit">Filtrlash</button><a class="btn link" href="{h(ctx.url(path))}">Tozalash</a>'
            f'{dl}</form>')


def num_inputs(f: dict, *, fit: bool = True) -> str:
    """Summa ($ dan/gacha), yutish % ≥, fit ≥ — the numeric filters shared by every table page."""
    def inp(name: str, ph: str, cls: str = "") -> str:
        val = f.get(name)
        v = "" if val is None else f"{val:g}"
        return (f'<input type="number" min="0" step="any" class="{cls}" name="{name}" value="{h(v)}" '
                f'placeholder="{h(ph)}" title="{h(ph)}">')
    out = (f'<span class="grp">Summa $ {inp("min", "dan")}–{inp("max", "gacha")}</span>'
           f'<span class="grp">Yutish ≥ {inp("win", "%", "sm")}</span>')
    if fit:
        out += f'<span class="grp">Fit ≥ {inp("fit", "ball", "sm")}</span>'
    return out


def stat(label: str, value, note: str = "", blue: bool = False) -> str:
    return (f'<div class="stat"><span>{h(label)}</span><b class="{"blue" if blue else ""}">{h(value)}</b>'
            f'<small>{h(note)}</small></div>')


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


VERDICT_PLAIN = {"QATNASHAMIZ": "Qatnashamiz", "SHARTLI": "Shartli", "YO'Q": "Yo'q"}


def _verdict_badge(lot: Lot) -> str:
    a = lot.analysis or {}
    if not a:
        return '<span class="pill">Tahlil kutilmoqda</span>'
    if a.get("error"):
        return '<span class="pill">Baholanmagan</span>'
    v = lot.verdict or "?"
    fit = f" · fit {h(a.get('fit_score'))}" if a.get("fit_score") is not None else ""
    return f'<span class="pill {VERDICT_CLASS.get(v, "")}">{h(VERDICT_PLAIN.get(v, v))}{fit}</span>'


def _price(lot: Lot) -> str:
    cost_usd = tc.usd(lot.start_cost, lot.currency, lot.usd_rate)
    usd = f" ({tc.fmt_usd(cost_usd)})" if cost_usd is not None else ""
    return h(tc.money_uzs(lot.start_cost, lot.currency) + usd)


def lot_actions(ctx: Ctx, lot: Lot) -> str:
    if not ctx.admin:
        return ""
    out = []
    if lot.status == LotStatus.BIDDING:
        out.append(_action(ctx, f"/lot/{lot.id}/unbid", "Qatnashmaymiz", "ghost"))
    else:
        out.append(_action(ctx, f"/lot/{lot.id}/bid", "Qatnashamiz"))
    if lot.status != LotStatus.IGNORED:
        out.append(_action(ctx, f"/lot/{lot.id}/ignore", "Kerak emas", "ghost"))
    return "".join(out)


def _sort_inputs(sort: str, direction: str) -> str:
    return (f'<input type="hidden" name="sort" value="{h(sort)}"><input type="hidden" name="dir" value="{h(direction)}">')


def _opt(value: str, current: str, label: str) -> str:
    return f'<option value="{h(value)}"{" selected" if value == current else ""}>{h(label)}</option>'


def _usd_short(v: float) -> str:
    if v >= 1e6:
        return f"${v / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    if v >= 1e4:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def tenders_page(ctx: Ctx, lots: list[Lot], *, q: str, verdict: str, status: str, counts: dict[str, int],
                 health: dict[str, dict], sort: str = "deadline", direction: str = "asc", nf: dict | None = None) -> str:
    from app.web.tables import LOT_COLS, table_html

    nf = nf or {}
    stats = (stat("Ochiq IT lotlar", counts.get("open", 0), "muddati o'tmaganlar")
             + stat("Qatnashsa bo'ladi", counts.get("good", 0), "AI xulosasi: qatnashamiz", blue=True)
             + stat("Shartli", counts.get("maybe", 0), "qo'shimcha tekshirish kerak")
             + stat("Qatnashyapmiz", counts.get("bidding", 0), "ariza topshirilmoqda"))
    inner = (f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi, buyurtmachi, lot raqami…">'
             f'<select name="v">{_opt("", verdict, "Barcha xulosalar")}{_opt("good", verdict, "Qatnashamiz")}'
             f'{_opt("maybe", verdict, "Shartli")}{_opt("bad", verdict, "Yo‘q")}</select>'
             f'<select name="s">{_opt("open", status, "Ochiqlar")}{_opt("bidding", status, "Qatnashayotganlar")}'
             f'{_opt("all", status, "Hammasi (yopilgan va IT emaslar bilan)")}</select>'
             f'{num_inputs(nf)}{_sort_inputs(sort, direction)}')
    xl = ctx.url("/lots.xlsx", q=q, v=verdict, s=status, sort=sort, dir=direction, **_nf_query(nf))
    down = [n for n, v in health.items() if v.get("alerted")]
    health_html = (f'<div class="flash err">Hozir ishlamayapti: {h(", ".join(down))}</div>' if down else "")
    table = table_html(ctx, "/lots", lots, LOT_COLS, sort=sort, direction=direction,
                       query={"q": q, "v": verdict, "s": status, **_nf_query(nf)},
                       detail=lambda lot: ctx.url(f"/lot/{lot.id}"),
                       bulk=[("bid", "Qatnashamiz", ""), ("ignore", "Kerak emas", "ghost"),
                             ("unbid", "Qatnashmaymiz", "ghost")], noun="lot")
    return layout(ctx, "tenders", f"""{health_html}<div class="stats">{stats}</div>
{filter_bar(ctx, "/lots", inner, xlsx=xl)}{table}""", title="Davlat IT tenderlari",
                  sub="Faqat IT xizmatlari · etender.uzex.uz va xarid.uzex.uz")


def _nf_query(nf: dict) -> dict:
    return {k: f"{v:g}" for k, v in nf.items() if v is not None}


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
        ("Holat", h(f"{str(tc.STATUS_UZ.get(lot.status, lot.status)).split(' ', 1)[-1]} · saytda: {lot.platform_status or '?'}")),
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
        reason = f"<p style=\"margin-top:0\">{h(a['verdict_reason_uz'])}</p>" if a.get("verdict_reason_uz") else ""
        extra = []
        tech = a.get("estimated_tech_score") or []
        if len(tech) == 2:
            extra.append(f"Taxminiy texnik ball: {tech[0]:g}–{tech[1]:g}")
        win = a.get("win_chance_percent") or []
        if len(win) == 2:
            extra.append(f"Yutish ehtimoli: {win[0]}–{win[1]}%")
        if a.get("hardware_or_server_required"):
            extra.append(f"Server/uskuna: {a['hardware_or_server_required']}")
        ai = (f'<h2>AI xulosasi {_verdict_badge(lot)}</h2><div class="panel">{reason}'
              + ("<ul>" + "".join(f"<li>{h(x)}</li>" for x in extra) + "</ul>" if extra else "") + "</div>")
        ai += bullets("To'siqlar", a.get("blockers"))
        ai += bullets("Risklar", a.get("risks"))
        ai += bullets("Qarama-qarshiliklar", a.get("contradictions"))
        ai += bullets("Buyurtmachiga savollar", a.get("questions_for_customer_uz"))
    elif a.get("error"):
        ai = '<div class="panel muted">AI bu lotni hali baholay olmadi.</div>'

    criteria = ""
    if lot.criteria:
        criteria = "<h2>Texnik mezonlar</h2><div class=\"panel\"><ul>" + "".join(
            f"<li>{h(c.get('label'))}"
            + (f" — <b>{h(format(c['max_score'], 'g'))} ball</b>" if c.get("max_score") is not None else "")
            + "</li>" for c in lot.criteria[:20]) + "</ul></div>"
    files = ""
    if lot.files:
        files = "<h2>Fayllar (asl saytda)</h2><div class=\"panel\"><ul>" + "".join(
            f"<li>{h(f.get('label'))}</li>" for f in lot.files) + "</ul></div>"
    desc = ""
    if lot.description:
        desc = f'<h2>Tavsif</h2><div class="panel prose">{h(tc.short(lot.description, 4000))}</div>'
    body = f"""<h1>{h(lot.title)}</h1>
<p class="sub">{h(a.get("summary_uz") or "")}</p>
<div class="actions" style="display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px">
{ext_link(lot.url, "Asl e'lonni saytda ochish", "btn open-site")}{lot_actions(ctx, lot)}</div>
<div class="panel"><dl class="kv">{kv}</dl></div>{ai}{criteria}
{bullets("Malaka talablari", lot.qualification)}{files}{desc}"""
    back = f'<a class="btn ghost" href="{h(ctx.url("/lots"))}">{icon("back")}Barcha lotlar</a>'
    return layout(ctx, "tenders", body, title=f"Lot {lot.external_id}", sub=lot.customer_name or "", tools=back)


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
        return '<span class="pill">Baholanmagan</span>'
    cls = "good" if job.fit >= 80 else "maybe" if job.fit >= 60 else "bad"
    win = f" · yutish {job.win_probability}%" if job.win_probability is not None else ""
    return f'<span class="pill {cls}">fit {job.fit}{h(win)}</span>'


def job_actions(ctx: Ctx, job: Job) -> str:
    if not ctx.admin:
        return ""
    out = []
    if job.status == JobStatus.AWAITING_APPROVAL:
        out.append(_action(ctx, f"/job/{job.id}/approve", "Taklifni tasdiqlash"))
    if job.status in (JobStatus.AWAITING_APPROVAL, JobStatus.SCORED, JobStatus.DRAFTED, JobStatus.FOUND):
        out.append(_action(ctx, f"/job/{job.id}/skip", "O'tkazib yuborish", "ghost"))
    return "".join(out)


def jobs_page(ctx: Ctx, jobs: list[Job], *, q: str, platform: str, show: str, platforms: list[str],
              counts: dict[str, int], sort: str = "posted", direction: str = "desc", nf: dict | None = None) -> str:
    from app.web.tables import JOB_COLS, table_html

    nf = nf or {}
    stats = (stat("Mos ishlar", counts.get("active", 0), "AI tavsiya qilganlar")
             + stat("Tasdiq kutmoqda", counts.get("awaiting", 0), "taklif tayyor", blue=True)
             + stat("Yuborilgan", counts.get("submitted", 0), "javob kutilmoqda")
             + stat("Olingan ishlar", counts.get("hired", 0), "ishlanmoqda yoki topshirilgan"))
    plat_opts = _opt("", platform, "Barcha platformalar") + "".join(
        _opt(p, platform, PLATFORM_NAME.get(p, p)) for p in platforms)
    inner = (f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi yoki tavsifi…">'
             f'<select name="p">{plat_opts}</select>'
             f'<select name="show">{_opt("good", show, "AI tavsiya qilganlar")}'
             f'{_opt("all", show, "Hammasi (o‘tkazilganlar bilan)")}</select>'
             f'{num_inputs(nf)}{_sort_inputs(sort, direction)}')
    xl = ctx.url("/jobs.xlsx", q=q, p=platform, show=show, sort=sort, dir=direction, **_nf_query(nf))
    table = table_html(ctx, "/jobs", jobs, JOB_COLS, sort=sort, direction=direction,
                       query={"q": q, "p": platform, "show": show, **_nf_query(nf)},
                       detail=lambda j: ctx.url(f"/job/{j.id}"),
                       bulk=[("approve", "Tasdiqlash", ""), ("skip", "O'tkazib yuborish", "ghost")], noun="ish")
    return layout(ctx, "jobs", f"""<div class="stats">{stats}</div>{filter_bar(ctx, "/jobs", inner, xlsx=xl)}{table}""",
                  title="Frilans ishlar", sub="FL.ru, Kwork, Freelancer.com, Upwork/Fiverr (Gmail)")


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
    summary = f'<h2>AI xulosasi {_fit_badge(job)}</h2><div class="panel">{h(job.summary_uz)}</div>' \
        if job.summary_uz else ""
    prop = ""
    if proposal is not None and ctx.admin:
        prop = (f'<h2>Taklif matni <span class="tag">v{proposal.version} · {h(money(proposal.price, proposal.currency))}'
                f'{f" · {proposal.timeline_days} kun" if proposal.timeline_days else ""}</span></h2>'
                f'<div class="panel prose">{h(proposal.text)}</div>')
    desc = f'<h2>Tavsif</h2><div class="panel prose">{h(tc.short(job.description, 6000))}</div>' \
        if job.description else ""
    body = f"""<h1>{h(job.title)}</h1>
<div class="actions" style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 16px">
{ext_link(job.url, "Asl e'lonni saytda ochish", "btn open-site")}{job_actions(ctx, job)}</div>
<div class="panel"><dl class="kv">{kv}</dl></div>{summary}{prop}{desc}"""
    back = f'<a class="btn ghost" href="{h(ctx.url("/jobs"))}">{icon("back")}Barcha ishlar</a>'
    return layout(ctx, "jobs", body, title=tc.short(job.title, 80),
                  sub=PLATFORM_NAME.get(job.platform, job.platform), tools=back)


# ── Orders: tenders + freelance in one table ────────────────────────────────
def orders_page(ctx: Ctx, rows: list, *, t: str, q: str, verdict: str, nf: dict, sort: str, direction: str) -> str:
    from app.web.tables import ORDER_COLS, table_html

    priced = [r.usd for r in rows if r.usd is not None]
    wins = [r.win for r in rows if r.win is not None]
    good = sum(r.verdict == "good" for r in rows)
    stats = (stat("Zakazlar", len(rows), f"{sum(r.kind == 'tender' for r in rows)} tender · "
                                         f"{sum(r.kind == 'freelance' for r in rows)} frilans")
             + stat("Umumiy summa", _usd_short(sum(priced)) if priced else "$0", f"{len(priced)} ta zakazda narx bor",
                    blue=True)
             + stat("O'rtacha yutish", f"{sum(wins) / len(wins):.0f}%" if wins else "—",
                    f"{len(wins)} ta zakaz baholangan")
             + stat("AI tavsiya qiladi", good, "qatnashamiz / fit ≥ 80"))
    seg = "".join(f'<a class="{"on" if key == t else ""}" href="{h(ctx.url("/", t=key, q=q, v=verdict, **_nf_query(nf)))}">'
                  f'{h(label)}</a>' for key, label in (("", "Hammasi"), ("tender", "Tenderlar"),
                                                       ("freelance", "Frilans")))
    inner = (f'<input type="hidden" name="t" value="{h(t)}">'
             f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi, buyurtmachi, platforma…">'
             f'<select name="v">{_opt("", verdict, "Barcha xulosalar")}{_opt("good", verdict, "Tavsiya (yaxshi)")}'
             f'{_opt("maybe", verdict, "Shartli / o‘rtacha")}{_opt("bad", verdict, "Yo‘q / past")}</select>'
             f'{num_inputs(nf)}{_sort_inputs(sort, direction)}')
    xl = ctx.url("/orders.xlsx", t=t, q=q, v=verdict, sort=sort, dir=direction, **_nf_query(nf))
    table = table_html(ctx, "/", rows, ORDER_COLS, sort=sort, direction=direction,
                       query={"t": t, "q": q, "v": verdict, **_nf_query(nf)}, detail=lambda r: r.detail,
                       bulk=[], noun="zakaz")
    return layout(ctx, "orders", f"""<div class="stats">{stats}</div>
{filter_bar(ctx, "/", inner, xlsx=xl)}{table}""", title="Zakazlar",
                  sub="Ochiq tenderlar va AI tavsiya qilgan frilans ishlar — bitta jadvalda",
                  tools=f'<div class="seg">{seg}</div>')


CATALOG_KINDS = (("goods", "Tovarlar"), ("service", "Xizmat / ish"), ("it", "IT"), ("all", "Hammasi"))


def catalog_page(ctx: Ctx, rows: list, *, kind: str, q: str, src: str, region: str, regions: list[str],
                 yon: str, dir_counts: dict[str, int], counts: dict[str, int], shown_limit: int, total: int, updated: dict, next_scan, nf: dict,
                 sort: str, direction: str, uzum: list | None = None, uzum_total: int = 0, big: float = 2.0) -> str:
    from app.web.tables import CATALOG_COLS, table_html

    def upd(name: str) -> str:
        at = updated.get(name)
        return f"{name}: {fmt_local(at)}" if at else f"{name}: hali yo'q"

    stats = (stat("Tovar yetkazib berish", counts.get("goods", 0), "ochiq lotlar, muddati o'tmagan", blue=True)
             + stat("Xizmat va ishlar", counts.get("service", 0), "qurilish, ta'mir, xizmat ko'rsatish")
             + stat("IT lotlar", counts.get("it", 0), "batafsil — «Tenderlar» bo'limida")
             + stat("Oxirgi yangilanish", fmt_local(max(updated.values())).split(" ")[-1] if updated else "—",
                    "keyingisi: " + (fmt_local(next_scan) if next_scan else "~30 daqiqada")))
    seg = "".join(
        f'<a class="{"on" if key == kind else ""}" href="{h(ctx.url("/all", k=key, q=q, src=src, r=region, y=yon, **_nf_query(nf)))}">'
        f'{h(label)} <span class="muted">{sum(counts.values()) if key == "all" else counts.get(key, 0)}</span></a>'
        for key, label in CATALOG_KINDS) + _uzum_tab(ctx, False)

    def money_inp(name: str, ph: str) -> str:
        val = nf.get(name)
        v = "" if val is None else f"{val:g}"
        return (f'<input type="number" min="0" step="any" name="{name}" value="{h(v)}" placeholder="{h(ph)}" '
                f'title="{h(ph)}">')

    from app.tenders.catalog import DIRECTION_LABEL

    region_opts = _opt("", region, "Barcha hududlar") + "".join(_opt(x, region, x) for x in regions)
    ordered = sorted(dir_counts, key=lambda k: (k == "other", -dir_counts[k]))
    if yon and yon not in dir_counts:
        ordered.append(yon)
    dir_opts = _opt("", yon, f"Barcha yo'nalishlar ({sum(dir_counts.values())})") + "".join(
        _opt(k, yon, f"{DIRECTION_LABEL.get(k, k)} ({dir_counts.get(k, 0)})") for k in ordered)
    inner = (f'<input type="hidden" name="k" value="{h(kind)}">'
             f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi, buyurtmachi, lot raqami…">'
             f'<select name="src">{_opt("", src, "Barcha manbalar")}{_opt("etender", src, "etender.uzex.uz")}'
             f'{_opt("xarid", src, "xarid.uzex.uz")}</select>'
             f'<select name="y" title="Yo\'nalish">{dir_opts}</select>'
             f'<select name="r">{region_opts}</select>'
             f'<span class="grp">Summa $ {money_inp("min", "dan")}–{money_inp("max", "gacha")}</span>'
             f'{_sort_inputs(sort, direction)}')
    xl = ctx.url("/all.xlsx", k=kind, q=q, src=src, r=region, y=yon, sort=sort, dir=direction, **_nf_query(nf))
    note = ""
    if total > shown_limit:
        note = (f'<p class="count">Jadvalda birinchi {shown_limit} tasi ko\'rsatilgan (jami {total}). '
                "Qidiruv yoki filtr bilan toraytiring — Excel'da hammasi bor.</p>")
    table = table_html(ctx, "/all", rows[:shown_limit], CATALOG_COLS, sort=sort, direction=direction,
                       query={"k": kind, "q": q, "src": src, "r": region, "y": yon, **_nf_query(nf)},
                       detail=lambda r: r.url, bulk=[], noun="lot", external=True)
    info = ('<p class="sub">Davlat xaridlari (etender.uzex.uz va xarid.uzex.uz) dagi barcha ochiq lotlar. '
            "Ro'yxat har 30 daqiqada avtomatik yangilanadi. Qatorni bosing — lot asl saytda ochiladi. "
            "xarid.uzex.uz ro'yxatida mahsulot nomi yo'q, faqat kategoriya ko'rinadi. "
            f'<span class="muted">({h(upd("etender"))} · {h(upd("xarid"))})</span></p>')
    uz = ""
    if uzum:
        from app.web.tables import price_cols
        cols = [c for c in price_cols(big) if c.key in ("name", "no", "qty", "tender", "uzum", "ratio", "over")]
        top = table_html(ctx, "/all/uzum", uzum, cols, sort="over", direction="desc", query={"f": "big"},
                         detail=lambda r: r.lot.url, bulk=[], noun="mahsulot", external=True)
        top = re.sub(r'<p class="count">[^<]*</p>', "", top, count=1)
        uz = (f'<h2 style="margin:18px 0 8px">🛒 Uzum narxi bilan katta farqlar '
              f'<span class="muted">— eng katta {len(uzum)} tasi (jami {uzum_total})</span> '
              f'<a class="btn ghost" href="{h(ctx.url("/all/uzum"))}">Hammasi →</a></h2>{top}'
              f'<h2 style="margin:22px 0 8px">Lotlar</h2>')
    return layout(ctx, "goods", f"""{info}<div class="stats">{stats}</div>{uz}
{filter_bar(ctx, "/all", inner, xlsx=xl)}{note}{table}""", title="Barcha tenderlar",
                  sub="Tovar yetkazib berish va boshqa davlat xaridlari · har 30 daqiqada yangilanadi",
                  tools=f'<div class="seg">{seg}</div>').replace(
        "<title>", '<meta http-equiv="refresh" content="900"><title>', 1)


def _uzum_tab(ctx: Ctx, on: bool) -> str:
    return f'<a class="{"on" if on else ""}" href="{h(ctx.url("/all/uzum"))}">🛒 Uzum narxlari</a>'


PRICE_FILTERS = (("big", "Katta farq"), ("over", "Tender qimmat"), ("under", "Tender arzon"), ("all", "Hammasi"))


def prices_page(ctx: Ctx, rows: list, *, f: str, q: str, yon: str, dir_counts: dict[str, int],
                counts: dict[str, int], state: dict, big: float, shown_limit: int, sort: str, direction: str,
                next_run) -> str:
    from app.tenders.catalog import DIRECTION_LABEL
    from app.web.tables import price_cols, table_html

    seg = "".join(f'<a href="{h(ctx.url("/all", k=key))}">{h(label)}</a>' for key, label in CATALOG_KINDS)
    seg += _uzum_tab(ctx, True)
    ftabs = "".join(
        f'<a class="{"on" if key == f else ""}" href="{h(ctx.url("/all/uzum", f=key, q=q, y=yon))}">'
        f'{h(label)} <span class="muted">{counts.get(key, 0)}</span></a>' for key, label in PRICE_FILTERS)
    at = state.get("at")
    last = fmt_local(datetime.fromisoformat(at)) if at else "hali yo'q"
    phone = state.get("phone") or "—"
    stats = (stat("Katta farq", counts.get("big", 0), f"tender ≥{big:g}× qimmat yoki ≤{big:g}× arzon", blue=True)
             + stat("Solishtirilgan", state.get("compared", 0), f"{state.get('piece', 0)} ta donalik mahsulotdan")
             + stat("Navbatda", state.get("pending", 0), "telefon hali Uzum'da qidirmagan")
             + stat("Oxirgi tekshiruv", last.split(" ")[-1] if at else "—",
                    ("telefon: ishlayapti" if phone == "ok" else phone)
                    + (f" · keyingisi {fmt_local(next_run)}" if next_run else "")))
    ordered = sorted(dir_counts, key=lambda k: (k == "other", -dir_counts[k]))
    if yon and yon not in dir_counts:
        ordered.append(yon)
    dir_opts = _opt("", yon, f"Barcha yo'nalishlar ({sum(dir_counts.values())})") + "".join(
        _opt(k, yon, f"{DIRECTION_LABEL.get(k, k)} ({dir_counts.get(k, 0)})") for k in ordered)
    inner = (f'<input type="hidden" name="f" value="{h(f)}">'
             f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: mahsulot, buyurtmachi, lot raqami…">'
             f'<select name="y" title="Yo\'nalish">{dir_opts}</select>{_sort_inputs(sort, direction)}')
    xl = ctx.url("/all/uzum.xlsx", f=f, q=q, y=yon, sort=sort, dir=direction)
    note = (f'<p class="count">Jadvalda birinchi {shown_limit} tasi (jami {len(rows)}) — Excel\'da hammasi bor.</p>'
            if len(rows) > shown_limit else "")
    table = table_html(ctx, "/all/uzum", rows[:shown_limit], price_cols(big), sort=sort, direction=direction,
                       query={"f": f, "q": q, "y": yon}, detail=lambda r: r.lot.url, bulk=[], noun="mahsulot",
                       external=True)
    info = ("<p class=\"sub\">Tovar lotlaridagi har bir mahsulotning <b>1 dona narxi</b> Uzum Market'dagi shu nomli "
            "mahsulotlarning o'rtacha narxi bilan solishtiriladi. Uzum'ni telefonning o'zi Chrome'da ochib qidiradi "
            "(har 30 daqiqada yangi mahsulotlar). Faqat dona/komplekt/pachka bilan o'lchanadigan mahsulotlar; "
            "nomi Uzum'dagi kamida 3 ta mahsulotga mos kelganlari ko'rsatiladi (20 baravardan ortiq farq — boshqa "
            "mahsulot deb hisoblanadi va chiqarilmaydi). «Uzum narxi» ustiga bosing — "
            "Uzum'dagi qidiruv ochiladi, qatorni bosing — lot ochiladi. "
            "<span class=\"muted\">Uzum chakana narxi; tenderda yetkazib berish, kafolat va QQS ham bo'lishi mumkin.</span></p>")
    return layout(ctx, "goods", f"""{info}<div class="stats">{stats}</div><div class="seg" style="margin:0 0 12px">{ftabs}</div>
{filter_bar(ctx, "/all/uzum", inner, xlsx=xl)}{note}{table}""", title="Uzum narxlari bilan solishtirish",
                  sub="Tender narxi ↔ Uzum Market · katta farqlar", tools=f'<div class="seg">{seg}</div>').replace(
        "<title>", '<meta http-equiv="refresh" content="900"><title>', 1)


def login_page(ctx: Ctx, *, error: str = "", next_url: str = "", user: str = "") -> str:
    if not ctx.login_enabled:
        body = '<div class="panel login">Sayt yopiq: serverda WEB_ADMIN_PASSWORD sozlanmagan.</div>'
        return layout(ctx, "", body, title="Kirish")
    err = f'<div class="flash err">{h(error)}</div>' if error else ""
    body = f"""<form class="panel login" method="post" action="{h(ctx.url("/login"))}">
<div class="logo">{h((ctx.title or "A")[:1].upper())}</div>
<h1>Kirish</h1><p class="sub" style="margin:0">Zakazlar, tenderlar va frilans ishlarni ko'rish uchun kiring.</p>{err}
<input type="hidden" name="next" value="{h(next_url)}">
<label>Login<input type="text" name="user" value="{h(user)}" autocomplete="username" autocapitalize="none"
required {"" if user else "autofocus"}></label>
<label>Parol<input type="password" name="password" autocomplete="current-password" required
{"autofocus" if user else ""}></label>
<button type="submit">Kirish</button></form>"""
    return layout(ctx, "", body, title="Kirish")


def profile_page(ctx: Ctx, *, user: str, session_until: str, stats: list[tuple[str, int]]) -> str:
    opts = "".join(
        f'<button type="submit" name="theme" value="{k}" class="{"on" if ctx.theme == k else ""}">'
        f'<span class="sw {k}"></span>{h(label)}{" ✓" if ctx.theme == k else ""}</button>'
        for k, label in THEMES.items())
    tiles = "".join(f'<div class="stat"><span>{h(label)}</span><b>{n}</b></div>' for label, n in stats)
    body = f"""<div class="panel prof"><div class="avatar">{h(user[:1].upper() or "A")}</div>
<div><h1>{h(user)}</h1><p class="sub" style="margin:0">Sayt egasi · sessiya {h(session_until)} gacha</p></div></div>
<h2>Ko'rinish</h2>
<form class="themes" method="post" action="{h(ctx.url("/theme"))}">
<input type="hidden" name="csrf" value="{h(ctx.csrf)}"><input type="hidden" name="back" value="{h(ctx.url("/profile"))}">
{opts}</form>
<p class="muted">"Avtomatik" — telefon yoki kompyuter sozlamasiga qarab o'zi qora/oq bo'ladi. Chap paneldagi
🌙/☀️ tugmasi ham istalgan paytda almashtiradi.</p>
<h2>Statistika</h2><div class="stats">{tiles}</div>
<h2>Hisob</h2><div class="panel"><dl class="kv"><dt>Login</dt><dd>{h(user)}</dd>
<dt>Sessiya tugashi</dt><dd>{h(session_until)}</dd><dt>Parolni o'zgartirish</dt>
<dd class="muted">Serverdagi .env faylida WEB_ADMIN_PASSWORD orqali (o'zgartirilsa hamma sessiyalar chiqib ketadi)</dd></dl>
<form method="post" action="{h(ctx.url("/logout"))}" style="margin-top:14px"><input type="hidden" name="csrf" value="{h(ctx.csrf)}">
<button class="ghost" type="submit">{icon("logout")}Chiqish</button></form></div>"""
    return layout(ctx, "profile", body, title="Profil")


# ── Telegram feed ───────────────────────────────────────────────────────────
KIND_LABEL = {"tender": "Tender", "freelance": "Frilans", "email": "Gmail", "status": "Holat"}


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
                    href, text, external = ctx.url(f"/lot/{parts[2]}"), "Batafsil", False
                elif parts[0] == "job":
                    href, text, external = ctx.url(f"/job/{parts[2]}"), "Batafsil", False
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
    tab_html = "".join(f'<a class="{"on" if k == kind else ""}" href="{h(ctx.url("/feed", k=k))}">{h(label)}</a>'
                       for k, label in tabs) if len(kinds) > 1 else ""
    items = "".join(f"""<article class="msg"><div class="body">{to_web(m.text)}</div>{_msg_buttons(ctx, m.buttons)}
<div class="when"><span>{h(KIND_LABEL.get(m.kind, m.kind))}</span><span>{h(fmt_local(m.created_at))}</span></div>
</article>""" for m in messages) or '<div class="empty">Hozircha xabar yo\'q.</div>'
    more = (f'<p><a class="btn ghost" href="{h(ctx.url("/feed", k=kind, before=next_before))}">Eskiroq xabarlar</a></p>'
            if next_before else "")
    body = f"""<div class="feed">{items}</div>{more}"""
    return layout(ctx, "feed", body, title="Telegram xabarlari",
                  sub="Bot yuborgan xabarlar · har 2 daqiqada yangilanadi",
                  tools=f'<div class="seg">{tab_html}</div>' if tab_html else "").replace(
        "<title>", '<meta http-equiv="refresh" content="120"><title>', 1)
