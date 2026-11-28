"""HTML rendering for the dashboard (Uzbek). Everything from the DB is untrusted → always escaped via h()."""

from __future__ import annotations

import contextlib
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
:root{color-scheme:light;
--bg:#f8fafd;--card:#ffffff;--soft:#f1f4f9;--field:#ffffff;--hover:#eef1f6;--active:#d3e3fd;--chip:#eef1f6;
--ink:#1f1f1f;--muted:#5f6368;--faint:#80868b;--line:#e3e6ea;--line2:#c4c7c5;
--side:#f8fafd;--side-ink:#1f1f1f;--side-muted:#5f6368;--side-hover:#e9edf3;--side-on:#d3e3fd;--side-on-ink:#041e49;
--brand:#0b57d0;--brand2:#0842a0;--brand-bg:#d3e3fd;--on-ink:#ffffff;--btn-hover:#0842a0;
--good:#146c2e;--good-bg:#e6f4ea;--good-line:#b7dfc1;--maybe:#8a5100;--maybe-bg:#fef3d6;
--bad:#b3261e;--bad-bg:#fce8e6;--bad-line:#f2b8b5;--bid:#7a1fa2;--bid-bg:#f3e8fd;
--topbg:rgba(248,250,253,.92);--seg-on:#ffffff;
--e1:0 1px 2px rgba(60,64,67,.3),0 1px 3px 1px rgba(60,64,67,.15);--e0:0 1px 2px rgba(60,64,67,.12);
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
:root[data-theme=dark]{color-scheme:dark;
--bg:#1b1b1b;--card:#282a2c;--soft:#303134;--field:#1f1f1f;--hover:#333537;--active:#004a77;--chip:#37393b;
--ink:#e3e3e3;--muted:#c4c7c5;--faint:#8e918f;--line:#3c4043;--line2:#5f6368;
--side:#1b1b1b;--side-ink:#e3e3e3;--side-muted:#c4c7c5;--side-hover:#2d2f31;--side-on:#004a77;--side-on-ink:#c2e7ff;
--brand:#a8c7fa;--brand2:#d3e3fd;--brand-bg:#0842a0;--on-ink:#062e6f;--btn-hover:#d3e3fd;
--good:#6dd58c;--good-bg:#0f3d21;--good-line:#1e5631;--maybe:#fdd663;--maybe-bg:#3d3000;
--bad:#f2b8b5;--bad-bg:#601410;--bad-line:#8c1d18;--bid:#e7b7ff;--bid-bg:#4a1a63;
--topbg:rgba(27,27,27,.9);--seg-on:#3c4043;
--e1:0 1px 2px rgba(0,0,0,.5),0 1px 3px 1px rgba(0,0,0,.3);--e0:0 1px 2px rgba(0,0,0,.4)}
.theme .sun,[data-theme=dark] .theme .moon{display:none}[data-theme=dark] .theme .sun{display:block}
aside form{margin:0}
.themes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;max-width:520px}
.themes button{height:auto;flex-direction:column;gap:8px;padding:12px;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:12px}
.themes button:hover{background:var(--hover)}.themes button.on{border-color:var(--brand);box-shadow:0 0 0 2px var(--brand-bg)}
.themes .sw{width:100%;height:44px;border-radius:8px;border:1px solid var(--line2)}
.sw.light{background:linear-gradient(90deg,#f8fafd 30%,#fff 30%)}.sw.dark{background:linear-gradient(90deg,#1b1b1b 30%,#282a2c 30%)}
.sw.auto{background:linear-gradient(135deg,#fff 50%,#1b1b1b 50%)}
.avatar{width:56px;height:56px;border-radius:50%;background:var(--brand);color:var(--on-ink);display:grid;place-content:center;font-size:22px;font-weight:500}
.prof{display:flex;gap:16px;align-items:center}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
::selection{background:var(--brand-bg)}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 Roboto,"Google Sans Text",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
a{color:var(--brand);text-decoration:none;text-underline-offset:3px}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
svg.i{width:20px;height:20px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.app{display:grid;grid-template-columns:256px minmax(0,1fr);min-height:100vh}.app>div{min-width:0}
aside{background:var(--side);color:var(--side-ink);padding:12px 12px 12px 8px;position:sticky;top:0;height:100vh;
display:flex;flex-direction:column;gap:2px;z-index:5;overflow:auto}
.logo{display:flex;align-items:center;gap:12px;color:var(--side-ink);font-size:18px;font-weight:400;padding:6px 12px 16px;line-height:1.2}
.logo i{width:32px;height:32px;border-radius:50%;background:var(--brand);color:var(--on-ink);display:grid;place-content:center;
font-weight:500;font-size:15px;font-style:normal;flex:none}
.logo span{overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;font-size:15px}
.logo:hover{text-decoration:none}
aside nav{display:flex;flex-direction:column;gap:1px}
aside nav .grp{font-size:12px;font-weight:500;color:var(--side-muted);padding:16px 16px 6px}
aside nav a{display:flex;align-items:center;gap:16px;height:36px;padding:0 16px;border-radius:999px;color:var(--side-ink);
font-weight:400;white-space:nowrap;font-size:14px}
aside nav a svg.i{color:var(--side-muted)}
aside nav a:hover{background:var(--side-hover);text-decoration:none}
aside nav a.on{background:var(--side-on);color:var(--side-on-ink);font-weight:600}aside nav a.on svg.i{color:var(--side-on-ink)}
aside .acts{display:flex;gap:4px;padding:8px 8px 0}
aside .out{position:relative;width:40px;height:40px;border-radius:50%;display:grid;place-content:center;
color:var(--side-muted);background:none;border:0;padding:0;cursor:pointer}
aside .out:hover{background:var(--side-hover);color:var(--side-ink);text-decoration:none}aside .out.on{background:var(--side-on);color:var(--side-on-ink)}
aside [data-tip]:hover::after{content:attr(data-tip);position:absolute;left:50%;bottom:46px;transform:translateX(-50%);
background:#3c4043;color:#fff;font-size:12px;padding:4px 8px;border-radius:4px;white-space:nowrap;pointer-events:none}
aside .grow{flex:1}
.top{height:64px;display:flex;align-items:center;gap:14px;padding:0 24px;position:sticky;top:0;background:var(--topbg);
backdrop-filter:blur(8px);z-index:4}
.top b{font-size:22px;font-weight:400;white-space:nowrap}.top .sub{color:var(--muted);font-size:13px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}.top .tools{margin-left:auto;display:flex;gap:8px;align-items:center}
main{padding:8px 24px 48px;min-width:0}
h1{font-size:22px;line-height:1.3;margin:0 0 6px;font-weight:400}
h2{font-size:16px;margin:24px 0 10px;font-weight:500;display:flex;gap:8px;align-items:center}
p.sub{color:var(--muted);margin:0 0 16px;max-width:80ch}
.stats{display:flex;flex-wrap:wrap;background:var(--card);border-radius:12px;box-shadow:var(--e0);border:1px solid var(--line);
margin:0 0 16px;overflow:hidden}
.stat{flex:1 1 170px;padding:12px 18px;border-left:1px solid var(--line)}.stat:first-child{border-left:0}
.stat span{color:var(--muted);font-size:13px;display:block}
.stat b{display:block;font-size:22px;font-weight:500;line-height:1.3;margin:4px 0 2px}
.stat b.blue{color:var(--good)}.stat small{color:var(--muted);font-size:12px;display:block}
form.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;align-items:center}
form.filters .grp{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:13px}
input,select,button,textarea{font:inherit;color:inherit}
input[type=text],input[type=search],input[type=password],input[type=number],input[type=url],select,textarea{background:var(--field);
border:1px solid var(--line2);border-radius:8px;padding:0 12px;height:40px;outline:none;font-size:14px}
textarea{padding:10px 12px}
::placeholder{color:var(--faint);opacity:1}
input[type=number]{width:96px}input[type=number].sm{width:72px}
input:focus,select:focus,textarea:focus{border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand)}
input[type=search]{flex:1;min-width:240px;border-radius:999px;background:var(--soft);border-color:transparent;padding:0 18px}
input[type=search]:focus{background:var(--field)}
form.filters select{max-width:100%}
button,.btn{background:var(--brand);color:var(--on-ink);border:0;border-radius:999px;padding:0 20px;height:40px;
font-weight:500;font-size:14px;cursor:pointer;display:inline-flex;align-items:center;gap:8px;white-space:nowrap;
transition:box-shadow .15s,background-color .15s}
button:hover,.btn:hover{background:var(--btn-hover);box-shadow:var(--e0);text-decoration:none}
button:disabled{opacity:.5;cursor:default;box-shadow:none}
.btn.ghost,button.ghost{background:transparent;color:var(--brand);border:1px solid var(--line2)}
.btn.ghost:hover,button.ghost:hover{background:var(--brand-bg);box-shadow:none}
.panel.item:target{border-color:var(--brand);box-shadow:0 0 0 2px var(--brand-bg)}
summary{cursor:pointer}
.dirs{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 16px;margin:0 0 12px}
.dirs>b{font-size:13px;font-weight:500;margin-right:8px}.dirs>.muted{font-size:12px}
.dirs .chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.filtbox{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 16px;margin:0 0 12px}
.crow{display:flex;gap:12px;align-items:flex-start;padding:8px 0}.crow+.crow{border-top:1px solid var(--line)}
.crow>b{font-size:13px;font-weight:500;min-width:130px;padding-top:6px}.crow .chips{display:flex;flex-wrap:wrap;gap:8px}
.chipf.zero{opacity:.45}
.chipf{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);border-radius:8px;height:32px;padding:0 12px;
font-size:13px;font-weight:500;color:var(--ink);background:var(--card)}
.chipf:hover{background:var(--hover);text-decoration:none}
.chipf span{font-size:12px;color:var(--muted)}
.chipf.on{background:var(--brand-bg);border-color:transparent;color:var(--side-on-ink)}.chipf.on span{color:var(--side-on-ink)}
[data-theme=dark] .chipf.on{color:var(--brand2)}
details.help{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 16px;margin:0 0 14px;
color:var(--muted);font-size:13px}details.help summary{font-weight:500;color:var(--ink)}details.help p{margin:8px 0 2px;max-width:90ch}
.home{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;align-items:start}
.home .card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px 16px;display:flex;
flex-direction:column;gap:14px}
.home .card h3{margin:0;font-size:16px;font-weight:500;display:flex;align-items:center;gap:10px}
.home .card h3 svg.i{color:var(--muted)}
.home .card h3 small{font-weight:400;color:var(--muted);font-size:12px;margin-left:auto}
.home .nums{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.home .nums div{background:var(--soft);border-radius:12px;padding:10px 12px;min-width:0}
.home .nums b{display:block;font-size:20px;font-weight:500;line-height:1.3;overflow-wrap:anywhere}.home .nums b.blue{color:var(--good)}
.home .nums span{font-size:12px;color:var(--muted)}
.home ol{min-width:0;margin:0;padding:0;list-style:none;display:grid;gap:0}
.home li{margin:0;min-width:0}.home li a{display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:8px;color:var(--ink)}
.home li a:hover{background:var(--hover);text-decoration:none}
.home li .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.home li .w{font-size:13px;font-weight:500;color:var(--muted);white-space:nowrap}
.home .card>.btn{align-self:flex-start}
.btn.xs{height:32px;padding:0 14px;font-size:13px;gap:6px}.btn.xs svg.i{width:16px;height:16px}
.btn.link{background:none;border:0;color:var(--brand)}.btn.link:hover{background:var(--brand-bg);box-shadow:none}
.seg{display:inline-flex;border:1px solid var(--line2);border-radius:999px;overflow:hidden}
.seg a{padding:7px 16px;color:var(--ink);font-size:13px;font-weight:500}.seg a+a{border-left:1px solid var(--line2)}
.seg a:hover{background:var(--hover);text-decoration:none}.seg a.on{background:var(--brand-bg);color:var(--side-on-ink)}
[data-theme=dark] .seg a.on{color:var(--brand2)}
.badge,.pill{display:inline-flex;align-items:center;gap:6px;border-radius:8px;padding:2px 10px;font-weight:500;
font-size:12px;white-space:nowrap;background:var(--chip);color:var(--muted)}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.badge.good,.pill.good{background:var(--good-bg);color:var(--good)}
.badge.maybe,.pill.maybe{background:var(--maybe-bg);color:var(--maybe)}
.badge.bad,.pill.bad,.pill.solid-bad{background:var(--bad-bg);color:var(--bad)}
.pill.blue{background:var(--brand-bg);color:var(--side-on-ink)}[data-theme=dark] .pill.blue{color:var(--brand2)}
.pill.bid{background:var(--bid-bg);color:var(--bid)}
.chip{background:var(--chip);border-radius:8px;padding:1px 8px;font-size:12px;color:var(--muted)}
.tag{font-size:12px;color:var(--muted);border:1px solid var(--line2);border-radius:8px;padding:1px 8px}
.lotno{font-family:var(--mono);font-size:12.5px}
.urgent{color:var(--bad)!important;font-weight:500}.soon{color:var(--maybe)!important;font-weight:500}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 20px;margin:0 0 12px}
dl.kv{display:grid;grid-template-columns:minmax(120px,200px) 1fr;gap:10px 16px;margin:0}
dl.kv dt{color:var(--muted)}dl.kv dd{margin:0}
ul{margin:4px 0;padding-left:20px}li{margin:3px 0}
.prose{white-space:pre-wrap;word-wrap:break-word;max-width:90ch}
.muted{color:var(--muted)}.empty{padding:48px;text-align:center;color:var(--muted)}
.flash{background:var(--good-bg);color:var(--good);border-radius:8px;padding:10px 16px;margin:0 0 14px;font-weight:500}
.flash.err{background:var(--bad-bg);color:var(--bad)}
footer{padding:0 24px 24px;color:var(--faint);font-size:12px}
.count{color:var(--muted);margin:0 0 8px;font-size:13px}
.bulk{display:none;gap:8px;align-items:center;margin:0 0 10px;padding:6px 8px 6px 16px;background:var(--brand-bg);color:var(--side-on-ink);
border-radius:12px}.bulk.show{display:flex}
.tablewrap{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;max-height:calc(100vh - 140px)}
table.grid{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px}
.grid th,.grid td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:middle}
.grid tbody tr:last-child td{border-bottom:0}
.grid thead th{position:sticky;top:0;background:var(--card);font-weight:500;color:var(--muted);z-index:2;font-size:12.5px}
.grid th a{color:var(--muted);display:flex;align-items:center;justify-content:space-between;gap:8px}
.grid th a:hover{text-decoration:none;color:var(--ink)}
.grid th .si{color:var(--faint);flex:none}.grid th.on .si,.grid th.on a{color:var(--brand)}
.grid tbody tr{cursor:pointer}.grid tbody tr:hover td{background:var(--hover);box-shadow:inset 0 -1px 0 var(--line)}
.grid tbody tr.sel td{background:var(--brand-bg)}
.grid td.num,.grid th.num{text-align:right}.grid th.num a{justify-content:flex-end}
.grid td.num{font-weight:500}
.grid td.wide{max-width:380px;overflow:hidden;text-overflow:ellipsis}
.grid td.mid{max-width:220px;overflow:hidden;text-overflow:ellipsis}
.grid td.dim{color:var(--muted);font-size:12px}
.grid tfoot td{background:var(--soft);font-weight:500;border-top:1px solid var(--line);border-bottom:0;position:sticky;bottom:0}
.grid .cb{width:44px;padding-right:0}
.grid input[type=checkbox]{appearance:none;width:18px;height:18px;border:2px solid var(--muted);border-radius:3px;
background:transparent;cursor:pointer;vertical-align:middle;margin:0;display:inline-grid;place-content:center}
.grid input[type=checkbox]:checked{background:var(--brand);border-color:var(--brand)}
.grid input[type=checkbox]:checked::after{content:"";width:9px;height:4px;border:2px solid var(--on-ink);border-top:0;
border-right:0;transform:rotate(-45deg) translate(1px,-1px)}
.bar{display:inline-flex;align-items:center;gap:8px}.bar i{display:inline-block;width:44px;height:4px;border-radius:2px;
background:var(--chip);overflow:hidden}.bar i b{display:block;height:100%;background:var(--brand);border-radius:2px}
.nowrap{white-space:nowrap}
.feed{display:grid;gap:10px;max-width:760px}
.msg{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px 16px 10px}
.msg .body{white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
.msg .body pre,.msg .body code{font-family:var(--mono);font-size:12.5px;background:var(--chip);border-radius:4px;padding:0 4px}
.msg .body blockquote{margin:4px 0;padding-left:10px;border-left:1px solid var(--line2);color:var(--muted)}
.msg .when{display:flex;justify-content:space-between;gap:8px;color:var(--faint);font-size:12px;margin-top:8px}
.kb{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.kb a{flex:1 1 auto;text-align:center;border:1px solid var(--line2);border-radius:999px;padding:5px 12px;font-weight:500;font-size:13px;color:var(--brand)}
.spoiler{background:var(--muted);color:transparent;border-radius:3px}.spoiler:hover{color:inherit;background:none}
.tabs{margin:0 0 14px}
.login{max-width:400px;margin:12vh auto 0;display:grid;gap:14px;padding:36px 40px;border-radius:28px}
.login .logo{width:48px;height:48px;border-radius:50%;background:var(--brand);color:var(--on-ink);display:grid;place-content:center;padding:0;font-size:20px}
.login h1{font-size:24px}
.login label{display:grid;gap:6px;color:var(--muted);font-size:13px}.login button{height:40px;justify-content:center}
.login input{height:44px!important}
.solo main{max-width:none}
/* table money: plain bold sum, $ under it as a quiet second line */
.ptag{display:inline-flex;flex-direction:column;align-items:flex-end;line-height:1.2}
.ptag .face{display:inline-flex;flex-direction:column;align-items:flex-end}
.ptag b{font-weight:500;white-space:nowrap}.ptag small{font-size:11.5px;color:var(--muted);white-space:nowrap}
.ptag.big b{font-size:22px;font-weight:500}
.stats .stat.money .ptag{display:inline-flex;margin:4px 0 2px}.stats .stat.money .ptag .face{display:inline-flex}
/* home: «Muddati yaqin lotlar» — a plain list */
.soonlist{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:0 0 20px;overflow:hidden}
.soonlist .head{display:flex;align-items:center;gap:12px;padding:14px 20px 10px}
.soonlist .head h2{margin:0;font-size:16px;font-weight:500}.soonlist .head .muted{font-size:13px}
.soonlist .head a{margin-left:auto;font-size:13px;font-weight:500}
.soonlist a.row{display:grid;grid-template-columns:minmax(0,1fr) 150px 150px 130px;gap:16px;align-items:center;
padding:11px 20px;border-top:1px solid var(--line);color:var(--ink)}
.soonlist a.row:hover{background:var(--hover);text-decoration:none}
.soonlist .t{min-width:0}.soonlist .t b{display:block;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.soonlist .t small{color:var(--muted);font-size:12px}
.soonlist .sum{text-align:right;font-weight:500}.soonlist .sum small{display:block;color:var(--muted);font-size:12px;font-weight:400}
.soonlist .left{font-size:13px}.soonlist .left small{display:block;color:var(--muted);font-size:12px}
.soonlist .v{justify-self:end}
.soonlist .none{padding:16px 20px;border-top:1px solid var(--line);color:var(--muted)}
.ai-est{display:inline-flex;align-items:center;gap:4px}.ai-est svg.i{width:15px;height:15px;color:var(--brand)}
details.help summary svg.i{width:16px;height:16px;vertical-align:-3px;margin-right:6px}
.chipf svg.i,.themes svg.i{width:14px;height:14px;stroke-width:2.2}
@media (max-width:760px){.app{grid-template-columns:1fr}aside{position:sticky;height:auto;flex-direction:row;
align-items:center;padding:6px 8px;overflow-x:auto;border-bottom:1px solid var(--line)}
.logo{padding:0 6px 0 4px}.logo span,aside nav .grp,aside .grow{display:none}aside nav{flex-direction:row}
aside nav a{padding:0 12px;gap:8px}aside .acts{padding:0}aside [data-tip]:hover::after{display:none}
.top{position:static;padding:0 16px;height:56px}.top .sub{display:none}
main{padding:8px 14px 32px}dl.kv{grid-template-columns:1fr}input[type=search]{min-width:100%}.stat{flex-basis:50%}
.soonlist .head{flex-wrap:wrap;gap:2px 12px}.soonlist .head .muted{order:3;flex-basis:100%}
.soonlist a.row{grid-template-columns:minmax(0,1fr) auto;gap:4px 12px}.soonlist .left{grid-column:1}.soonlist .v{grid-column:2;grid-row:2}}
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
    "cart": '<path d="M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h9.2a1 1 0 0 0 1-.8L20 8H6.2"/><circle cx="9" cy="20" r="1.3"/><circle cx="17" cy="20" r="1.3"/>',
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
    tabs = [("", "orders", ctx.url("/"), "orders", "Bosh sahifa"),
            ("Davlat tenderlari", "tenders", ctx.url("/lots"), "tenders", "IT tenderlar"),
            ("", "goods", ctx.url("/all"), "goods", "Tovar va xizmat"),
            ("", "uzum", ctx.url("/all/uzum"), "cart", "Uzum narxlari"),
            ("", "chat", ctx.url("/chat"), "bot", "AI chat"),
            ("Frilans", "jobs", ctx.url("/jobs"), "jobs", "Frilans ishlar"),
            ("Boshqa", "feed", ctx.url("/feed"), "feed", "Telegram xabarlar")]
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
    nav = "".join((f'<span class="grp">{h(grp)}</span>' if grp else "")
                  + f'<a class="{"on" if key == active else ""}" href="{h(url)}">{icon(ic)}<span>{h(label)}</span></a>'
                  for grp, key, url, ic, label in tabs)
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
<aside><a class="logo" href="{h(ctx.url("/"))}" title="{h(ctx.title)}"><i>{initial}</i><span>{h(ctx.title)}</span></a>
<nav>{nav}</nav><div class="grow"></div><div class="acts">{theme_btn}{profile}{logout}</div></aside>
<div>{top}<main>{flash_html}{body}</main>
<footer>Tenderlar har 20 daqiqada, frilans ishlar har 5–10 daqiqada yangilanadi · Toshkent vaqti ·
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


def chip_group(ctx: Ctx, path: str, param: str, title: str, chosen_csv: str,
               options: list[tuple[str, str, int]], **query) -> str:
    """A row of toggle buttons for one filter (?param=a,b); «Hammasi» clears it. Counts on every button."""
    chosen = {k for k in chosen_csv.split(",") if k}

    def link(sel: set[str]) -> str:
        return h(ctx.url(path, **{param: ",".join(sorted(sel))}, **query))

    chips = [f'<a class="chipf{"" if chosen else " on"}" href="{link(set())}">Hammasi</a>']
    for k, label, n in options:
        on = k in chosen
        chips.append(f'<a class="chipf{" on" if on else ""}{" zero" if not n and not on else ""}" '
                     f'href="{link(chosen ^ {k})}">{icon("check") if on else ""}{h(label)} <span>{n}</span></a>')
    return f'<div class="crow"><b>{h(title)}</b><div class="chips">{"".join(chips)}</div></div>'


def money_chips(ctx: Ctx, path: str, chosen: dict[str, str], band_counts: dict | None, **query) -> str:
    """«Boshlang'ich summa», «Zakalat» and «Avans» button rows (several bands at once in each)."""
    from app.web.tables import BAND_GROUPS, BAND_TITLES

    query = {k: v for k, v in query.items() if k not in BAND_GROUPS}
    rows = []
    for param, bands in BAND_GROUPS.items():
        others = {k: v for k, v in chosen.items() if k != param}
        counts = (band_counts or {}).get(param, {})
        rows.append(chip_group(ctx, path, param, BAND_TITLES[param], chosen.get(param, ""),
                               [(b[0], b[1], counts.get(b[0], 0)) for b in bands], **others, **query))
    return f'<div class="filtbox">{"".join(rows)}</div>'


def dir_chips(ctx: Ctx, path: str, yon: str, dir_counts: dict[str, int], **query) -> str:
    """Yo'nalish filter: one button per direction (with its count); click to add / remove, several at once."""
    from app.tenders.catalog import DIRECTION_LABEL

    chosen = {k for k in yon.split(",") if k}
    keys = sorted(dir_counts, key=lambda k: (k == "other", -dir_counts[k]))
    keys += sorted(chosen - set(keys))

    def link(sel: set[str]) -> str:
        return h(ctx.url(path, y=",".join(sorted(sel)), **query))

    chips = [f'<a class="chipf{"" if chosen else " on"}" href="{link(set())}">Hammasi '
             f'<span>{sum(dir_counts.values())}</span></a>']
    for k in keys:
        on = k in chosen
        chips.append(f'<a class="chipf{" on" if on else ""}" href="{link(chosen ^ {k})}" '
                     f'title="{"Olib tashlash" if on else "Qo‘shish"}">{icon("check") if on else ""}{h(DIRECTION_LABEL.get(k, k))} '
                     f'<span>{dir_counts.get(k, 0)}</span></a>')
    hint = (f'{len(chosen)} ta tanlangan — yana bosib qo\'shing yoki olib tashlang' if chosen
            else "bittasini yoki bir nechtasini bosing")
    return (f'<div class="dirs"><b>Yo\'nalish</b><span class="muted">{h(hint)}</span>'
            f'<div class="chips">{"".join(chips)}</div></div>')


def help_box(*paras: str, title: str = "Bu sahifa qanday ishlaydi?") -> str:
    """Long explanations stay folded so the page shows the numbers and the table first (paras are HTML)."""
    body = "".join(f"<p>{x}</p>" for x in paras if x)
    return f'<details class="help"><summary>{icon("info")}{h(title)}</summary>{body}</details>' if body else ""


def stat(label: str, value, note: str = "", blue: bool = False, money: bool = False) -> str:
    if money:  # the money total is written on a price tag, its caption on its own line under it
        return (f'<div class="stat money"><span>{h(label)}</span><span class="ptag big"><span class="face">'
                f'<b>{h(value)}</b></span></span><small>{h(note)}</small></div>')
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
                 health: dict[str, dict], sort: str = "deadline", direction: str = "asc", nf: dict | None = None,
                 sm: str = "", zk: str = "", av: str = "", band_counts: dict | None = None) -> str:
    from app.web.tables import LOT_COLS, pledge_uzs, table_html

    nf = nf or {}
    with_pledge = [lot for lot in lots if pledge_uzs(lot)]
    pledge_total = sum(pledge_uzs(lot) for lot in with_pledge)
    unknown = sum(1 for lot in lots if lot.pledge_percent and not pledge_uzs(lot))
    biggest = max(with_pledge, key=pledge_uzs, default=None)
    stats = (stat("Hammasida qatnashish uchun", tc.money_uzs(pledge_total),
                  f"jadvaldagi {len(lots)} ta lot zakalati · {len(with_pledge)} tasida bor", money=True)
             + stat("Ochiq IT lotlar", counts.get("open", 0), "muddati o'tmaganlar")
             + stat("Qatnashsa bo'ladi", counts.get("good", 0), "AI xulosasi: qatnashamiz", blue=True)
             + stat("Shartli", counts.get("maybe", 0), "qo'shimcha tekshirish kerak")
             + stat("Qatnashyapmiz", counts.get("bidding", 0), "ariza topshirilmoqda"))
    inner = (f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi, buyurtmachi, lot raqami…">'
             f'<select name="v">{_opt("", verdict, "Barcha xulosalar")}{_opt("good", verdict, "Qatnashamiz")}'
             f'{_opt("maybe", verdict, "Shartli")}{_opt("bad", verdict, "Yo‘q")}</select>'
             f'<select name="s">{_opt("open", status, "Ochiqlar")}{_opt("bidding", status, "Qatnashayotganlar")}'
             f'{_opt("all", status, "Hammasi (yopilgan va IT emaslar bilan)")}</select>'
             f'<input type="hidden" name="sm" value="{h(sm)}"><input type="hidden" name="zk" value="{h(zk)}"><input type="hidden" name="av" value="{h(av)}">'
             f'{num_inputs(nf)}{_sort_inputs(sort, direction)}')
    xl = ctx.url("/lots.xlsx", q=q, v=verdict, s=status, sm=sm, zk=zk, av=av, sort=sort, dir=direction, **_nf_query(nf))
    down = [n for n, v in health.items() if v.get("alerted")]
    health_html = (f'<div class="flash err">Hozir ishlamayapti: {h(", ".join(down))}</div>' if down else "")
    table = table_html(ctx, "/lots", lots, LOT_COLS, sort=sort, direction=direction,
                       query={"q": q, "v": verdict, "s": status, "sm": sm, "zk": zk, "av": av, **_nf_query(nf)},
                       detail=lambda lot: ctx.url(f"/lot/{lot.id}"),
                       bulk=[("bid", "Qatnashamiz", ""), ("ignore", "Kerak emas", "ghost"),
                             ("unbid", "Qatnashmaymiz", "ghost")], noun="lot",
                       foot={"title": f"Jami: {len(lots)} ta lot", "pledge": f"<b>{h(tc.money_uzs(pledge_total))}</b>"}
                       if lots else None)
    money_note = ("«Kiritiladigan pul» — <b>zakalat</b>: ariza berganda birjadagi hisobingizda "
                  "bloklanadi, yutmasangiz qaytariladi, yutsangiz shartnoma tuzilguncha turadi. Jadvaldagi hamma "
                  f"lotlarda qatnashish uchun jami <b>{h(tc.money_uzs(pledge_total))}</b> kerak"
                  + (f" (eng kattasi: lot {h(biggest.external_id)} — {h(tc.money_uzs(pledge_uzs(biggest)))})" if biggest else "")
                  + (f"; {unknown} ta lotning zakalati so'mda hisoblanmadi" if unknown else "")
                  + ". Birja komissiyasi bunga kirmaydi.")
    mchips = money_chips(ctx, "/lots", {"sm": sm, "zk": zk, "av": av}, band_counts, q=q, v=verdict, s=status, **_nf_query(nf))
    return layout(ctx, "tenders", f"""{health_html}{mchips}<div class="stats">{stats}</div>{help_box(money_note)}
{filter_bar(ctx, "/lots", inner, xlsx=xl)}{table}""", title="IT tenderlar",
                  sub=f"Davlat xaridlari: faqat IT xizmatlari · {tc.ALL_SITES}")


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
{ext_link(lot.url, "Asl e'lonni saytda ochish", "btn open-site")}{chat_button(ctx, f"{lot.source}-{lot.external_id}", "btn ghost")}{lot_actions(ctx, lot)}</div>
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
def _home_card(icon_txt: str, title: str, note: str, nums: list[tuple[str, str, bool]], items: list[tuple[str, str, str]],
               more: tuple[str, str], empty: str) -> str:
    """One section of the start page: 3 numbers, the best few items, a link to the full table."""
    nums_html = "".join(f'<div><b class="{"blue" if blue else ""}">{h(v)}</b><span>{h(k)}</span></div>'
                        for k, v, blue in nums)
    lis = "".join(f'<li><a href="{h(url)}"><span class="t" title="{h(t)}">{h(t)}</span><span class="w">{h(w)}</span></a></li>'
                  for t, w, url in items) or f'<li class="muted" style="padding:7px 8px">{h(empty)}</li>'
    return (f'<section class="card"><h3>{icon_txt} {h(title)}<small>{h(note)}</small></h3>'
            f'<div class="nums">{nums_html}</div><div><div class="count" style="margin:0 0 4px">Eng yaxshilari</div>'
            f'<ol>{lis}</ol></div><a class="btn ghost" href="{h(more[0])}">{h(more[1])} →</a></section>')


_STALL_VERDICT = {"good": ("good", "Qatnashamiz"), "maybe": ("maybe", "Shartli"), "bad": ("bad", "Yo'q")}


def soon_list(ctx: Ctx, it: list, now: datetime) -> str:
    """«Muddati yaqin lotlar»: the open IT lots closing first — title, sum, time left, AI verdict."""
    rows = sorted((r for r in it if r.when), key=lambda r: r.when)[:6]
    head = (f'<div class="head"><h2>Muddati yaqin lotlar</h2><span class="muted">{len(it)} ta ochiq IT lot</span>'
            f'<a href="{h(ctx.url("/lots"))}">Hammasini ko\'rish</a></div>')
    if not rows:
        return (f'<section class="soonlist">{head}<div class="none">Hozir ochiq IT lot yo\'q. Bot har 20 daqiqada '
                "beshta saytni tekshiradi.</div></section>")
    items = []
    for r in rows:
        left = (r.when - now).total_seconds()
        urg = "urgent" if left < 86400 else "soon" if left < 3 * 86400 else ""
        cls, label = _STALL_VERDICT.get(r.verdict, ("", "Tahlil kutilmoqda"))
        if "Qatnash" in (r.status_text or ""):
            cls, label = "bid", "Qatnashyapmiz"
        usd = tc.fmt_usd(r.usd) if r.usd is not None else ""
        items.append(
            f'<a class="row" href="{h(r.detail)}"><span class="t"><b title="{h(r.title)}">{h(r.title)}</b>'
            f'<small>{h(r.source)}</small></span>'
            f'<span class="sum">{h(r.price)}<small>{h(usd)}</small></span>'
            f'<span class="left"><span class="{urg}">{h(remaining_uz(r.when, now))}</span><small>{h(fmt_local(r.when))}</small></span>'
            f'<span class="v"><span class="pill {cls}">{h(label)}</span></span></a>')
    return f'<section class="soonlist">{head}{"".join(items)}</section>'


def home_page(ctx: Ctx, *, it: list, goods: list, jobs: list, goods_pledge: float) -> str:
    """Start page: IT tenders, goods/service tenders and freelance jobs side by side — never in one list."""
    def best(rows, key):
        return sorted(rows, key=key)[:5]

    def win_txt(w) -> str:
        return f"{w}%" if w is not None else ""

    it_good = [r for r in it if r.verdict in ("good", "maybe")]
    it_card = _home_card(icon("tenders"), "IT tenderlar", "davlat xaridlari", [
        ("ochiq lot", str(len(it)), False), ("qatnashsa bo'ladi", str(sum(r.verdict == "good" for r in it)), True),
        ("shartli", str(sum(r.verdict == "maybe" for r in it)), False)],
        [(r.title, win_txt(r.win) or r.price, r.detail) for r in best(it_good, lambda r: -(r.win or 0))],
        (ctx.url("/lots"), "Hamma IT tenderlar"), "Hozircha tavsiya qilingan IT lot yo'q")
    g_ok = [r for r in goods if r.ai_verdict in ("QATNASHISH MUMKIN", "SHARTLI")]
    goods_card = _home_card(icon("goods"), "Tovar va xizmat", "davlat xaridlari", [
        ("ochiq lot", str(len(goods)), False),
        ("AI: qatnashsa bo'ladi", str(sum(r.ai_verdict == "QATNASHISH MUMKIN" for r in goods)), True),
        ("zakalat (hammasi)", tc.money_uzs(goods_pledge).replace(" so'm", ""), False)],
        [((f"{r.customer_name} · {r.title}" if r.is_category else f"{r.title} · {r.customer_name}")
          if r.customer_name else r.title,
          " · ".join(x for x in (win_txt(r.win[0] if r.win else None), tc.money_uzs(r.start_cost, r.currency)) if x),
          ctx.url(f"/all/uzum/lot/{r.id}"))
         for r in best(g_ok, lambda r: (r.ai_verdict != "QATNASHISH MUMKIN", -(r.win[0] if r.win else 0),
                                        -(r.usd or 0)))],
        (ctx.url("/all"), "Hamma tovar va xizmat tenderlari"), "AI hali tovar lotlarini tahlil qilmoqda")
    j_good = [r for r in jobs if r.verdict in ("good", "maybe")]
    jobs_card = _home_card(icon("jobs"), "Frilans", "FL.ru, Kwork, Upwork…", [
        ("ochiq ish", str(len(jobs)), False), ("juda mos (fit ≥ 80)", str(sum(r.verdict == "good" for r in jobs)), True),
        ("o'rtacha mos", str(sum(r.verdict == "maybe" for r in jobs)), False)],
        [(r.title, f"fit {r.fit}" if r.fit is not None else r.price, r.detail)
         for r in best(j_good, lambda r: -(r.fit or 0))],
        (ctx.url("/jobs"), "Hamma frilans ishlar"), "Hozircha mos frilans ish yo'q")
    return layout(ctx, "orders", f'{soon_list(ctx, it, now_utc())}<div class="home">{it_card}{goods_card}{jobs_card}</div>',
                  title="Bosh sahifa", sub="Bugun nima yangi, nima mos, qancha pul va qachon yopiladi")


CATALOG_KINDS = (("goods", "Tovarlar"), ("service", "Xizmat / ish"))


def catalog_page(ctx: Ctx, rows: list, *, kind: str, q: str, src: str, region: str, regions: list[str],
                 yon: str, dir_counts: dict[str, int], counts: dict[str, int], shown_limit: int, total: int, updated: dict, next_scan, nf: dict,
                 sm: str = "", zk: str = "", av: str = "", band_counts: dict | None = None,
                 sort: str, direction: str, uzum: list | None = None, uzum_total: int = 0, big: float = 2.0) -> str:
    from app.web.tables import CATALOG_COLS, table_html

    def upd(name: str) -> str:
        at = updated.get(name)
        return f"{name}: {fmt_local(at)}" if at else f"{name}: hali yo'q"

    known = [r for r in rows if r.pledge_uzs is not None]
    pledge_total = sum(r.pledge_uzs or 0 for r in known)
    with_pledge = sum(1 for r in known if r.pledge_uzs)
    good = sum(1 for r in rows if r.ai_verdict == "QATNASHISH MUMKIN")
    goods_rows = [r for r in rows if r.kind == "goods"]
    stats = (stat("Hammasida qatnashish uchun", tc.money_uzs(pledge_total),
                  f"{len(known)} ta lot zakalati · {with_pledge} tasida bor", money=True)
             + stat("Tovar yetkazib berish", counts.get("goods", 0), "ochiq lotlar, muddati o'tmagan")
             + stat("AI: qatnashsa bo'ladi", good, f"{sum(1 for r in goods_rows if r.ai_verdict)} ta lot tahlil qilingan")
             + stat("Oxirgi yangilanish", fmt_local(max(updated.values())).split(" ")[-1] if updated else "—",
                    "keyingisi: " + (fmt_local(next_scan) if next_scan else "~30 daqiqada")))
    seg = "".join(
        f'<a class="{"on" if key == kind else ""}" href="{h(ctx.url("/all", k=key, q=q, src=src, r=region, y=yon, sm=sm, zk=zk, av=av, **_nf_query(nf)))}">'
        f'{h(label)} <span class="muted">{counts.get(key, 0)}</span></a>'
        for key, label in CATALOG_KINDS)

    def money_inp(name: str, ph: str) -> str:
        val = nf.get(name)
        v = "" if val is None else f"{val:g}"
        return (f'<input type="number" min="0" step="any" name="{name}" value="{h(v)}" placeholder="{h(ph)}" '
                f'title="{h(ph)}">')

    from app.tenders.catalog import DIRECTION_LABEL

    region_opts = _opt("", region, "Barcha hududlar") + "".join(_opt(x, region, x) for x in regions)
    inner = (f'<input type="hidden" name="k" value="{h(kind)}">'
             f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: nomi, buyurtmachi, lot raqami…">'
             f'<select name="src">{_opt("", src, "Barcha manbalar")}'
             + "".join(_opt(k, src, v) for k, v in tc.SOURCE_SITE.items()) + "</select>"
             f'<input type="hidden" name="y" value="{h(yon)}"><input type="hidden" name="sm" value="{h(sm)}">'
             f'<input type="hidden" name="zk" value="{h(zk)}"><input type="hidden" name="av" value="{h(av)}">'
             f'<select name="r">{region_opts}</select>'
             f'<span class="grp">Summa $ {money_inp("min", "dan")}–{money_inp("max", "gacha")}</span>'
             f'{_sort_inputs(sort, direction)}')
    xl = ctx.url("/all.xlsx", k=kind, q=q, src=src, r=region, y=yon, sm=sm, zk=zk, av=av, sort=sort, dir=direction, **_nf_query(nf))
    note = ""
    if total > shown_limit:
        note = (f'<p class="count">Jadvalda birinchi {shown_limit} tasi ko\'rsatilgan (jami {total}). '
                "Qidiruv yoki filtr bilan toraytiring — Excel'da hammasi bor.</p>")
    table = table_html(ctx, "/all", rows[:shown_limit], CATALOG_COLS, sort=sort, direction=direction,
                       query={"k": kind, "q": q, "src": src, "r": region, "y": yon, "sm": sm, "zk": zk, "av": av, **_nf_query(nf)},
                       detail=lambda r: ctx.url(f"/all/uzum/lot/{r.id}") if r.kind == "goods" else r.url,
                       bulk=[], noun="lot", external=lambda r: r.kind != "goods",
                       foot={"title": f"Jami: {len(rows)} ta lot", "pledge": f"<b>{h(tc.money_uzs(pledge_total))}</b>"}
                       if rows else None)
    pending = sum(1 for r in goods_rows if r.pledge_uzs is None)
    base = {"k": kind, "q": q, "src": src, "r": region, "y": yon, **_nf_query(nf)}
    mchips = money_chips(ctx, "/all", {"sm": sm, "zk": zk, "av": av}, band_counts, **base)
    money_note = ("«Kiritiladigan pul» — <b>zakalat</b>: ariza berganda birjadagi hisobingizda "
                  "bloklanadi, yutmasangiz qaytariladi. Filtrdagi hamma lotlarda qatnashish uchun jami "
                  f"<b>{h(tc.money_uzs(pledge_total))}</b> kerak (birja komissiyasi kirmaydi)."
                  + (f" {pending} ta lotning kartasi hali o'qilmagan («…») — har 30 daqiqada navbatdagilari o'qiladi."
                     if pending else "")
                  + " «AI xulosa» va «Yutish %» — tovar lotini AI yetkazib beruvchi ko'zi bilan baholaydi "
                  "(har 30 daqiqada yangi lotlar). xarid.uzex.uz va Hayot birja lotlarida zakalat ko'rsatilmaydi.")
    info = (f"Davlat xaridlari ({tc.ALL_SITES}) dagi ochiq tovar va xizmat lotlari "
            "(IT lotlar alohida — «IT tenderlar» bo'limida). "
            "Ro'yxat har 30 daqiqada avtomatik yangilanadi. Xizmat lot qatorini bossangiz — lot asl saytda ochiladi. "
            "Tovar lotini bossangiz — lot haqida batafsil ma'lumot va AI xulosasi ochiladi. "
            "xarid.uzex.uz ro'yxatida mahsulot nomi yo'q, faqat kategoriya ko'rinadi. "
            "xarid auksion va Hayot birja ro'yxati A23 telefonining Chrome'ida o'qiladi. "
            f'<span class="muted">({h(" · ".join(upd(k) for k in tc.SOURCE_NAME if k in updated))})</span>')
    uz = ""
    if uzum:
        from app.web.tables import gap_lot_cols, price_cols
        pc = {c.key: c for c in price_cols(big)}
        gl = {c.key: c for c in gap_lot_cols()}  # the lot's money and verdict right after the item name
        cols = ([pc["name"], gl["lprice"], gl["verdict"]] + [pc[k] for k in ("tender", "uzum", "ratio", "over", "no", "qty")]
                + [gl["pledge"], gl["win"], pc["go"]])
        top = table_html(ctx, "/all/uzum", uzum, cols, sort="over", direction="desc", query={"f": "big"},
                         detail=lambda r: ctx.url(f"/all/uzum/lot/{r.lot.id}"), bulk=[], noun="mahsulot")
        top = re.sub(r'<p class="count">[^<]*</p>', "", top, count=1)
        uz = (f'<h2 style="margin:18px 0 8px">{icon("cart")}Uzum narxi bilan katta farqlar '
              f'<span class="muted">— eng katta {len(uzum)} tasi (jami {uzum_total})</span> '
              f'<a class="btn ghost" href="{h(ctx.url("/all/uzum"))}">Hammasi →</a></h2>{top}'
              f'<h2 style="margin:22px 0 8px">Lotlar</h2>')
    return layout(ctx, "goods", f"""{dir_chips(ctx, "/all", yon, dir_counts, k=kind, q=q, src=src, r=region, sm=sm, zk=zk, av=av, **_nf_query(nf))}{mchips}
<div class="stats">{stats}</div>{help_box(info, money_note)}{uz}
{filter_bar(ctx, "/all", inner, xlsx=xl)}{note}{table}""", title="Tovar va xizmat tenderlari",
                  sub="Davlat xaridlari: tovar yetkazib berish va xizmatlar (IT alohida) · har 30 daqiqada yangilanadi",
                  tools=f'<div class="seg">{seg}</div>').replace(
        "<title>", '<meta http-equiv="refresh" content="900"><title>', 1)


PRICE_FILTERS = (("big", "Katta farq"), ("over", "Tender qimmat (Uzum arzon)"), ("under", "Tender arzon (Uzum qimmat)"),
                 ("all", "Hamma solishtirilgan"), ("lots", "Lot bo'yicha"))


def prices_page(ctx: Ctx, rows: list, *, f: str, q: str, yon: str, dir_counts: dict[str, int],
                sm: str = "", zk: str = "", av: str = "", band_counts: dict | None = None,
                counts: dict[str, int], state: dict, big: float, shown_limit: int, sort: str, direction: str,
                next_run) -> str:
    from app.tenders.catalog import DIRECTION_LABEL
    from app.web.tables import lot_price_cols, price_cols, table_html

    ftabs = "".join(
        f'<a class="{"on" if key == f else ""}" href="{h(ctx.url("/all/uzum", f=key, q=q, y=yon, sm=sm, zk=zk, av=av))}">'
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
    inner = (f'<input type="hidden" name="f" value="{h(f)}">'
             f'<input type="search" name="q" value="{h(q)}" placeholder="Qidirish: mahsulot, buyurtmachi, lot raqami…">'
             f'<input type="hidden" name="y" value="{h(yon)}"><input type="hidden" name="sm" value="{h(sm)}">'
             f'<input type="hidden" name="zk" value="{h(zk)}"><input type="hidden" name="av" value="{h(av)}">{_sort_inputs(sort, direction)}')
    xl = ctx.url("/all/uzum.xlsx", f=f, q=q, y=yon, sm=sm, zk=zk, av=av, sort=sort, dir=direction)
    note = (f'<p class="count">Jadvalda birinchi {shown_limit} tasi (jami {len(rows)}) — Excel\'da hammasi bor.</p>'
            if len(rows) > shown_limit else "")
    lots = f == "lots"
    table = table_html(ctx, "/all/uzum", rows[:shown_limit], lot_price_cols(big) if lots else [c for c in price_cols(big) if c.key != "status"],
                       sort=sort, direction=direction, query={"f": f, "q": q, "y": yon, "sm": sm, "zk": zk, "av": av},
                       detail=lambda r: ctx.url(f"/all/uzum/lot/{r.lot.id}"), bulk=[],
                       noun="lot" if lots else "mahsulot")
    info = ("Tovar lotlaridagi har bir mahsulotning <b>1 dona narxi</b> Uzum Market'dagi shu nomli "
            "mahsulotlarning o'rtacha narxi bilan solishtiriladi. Uzum'ni telefonning o'zi Chrome'da ochib qidiradi "
            "(har 30 daqiqada yangi mahsulotlar). Faqat dona/komplekt/pachka bilan o'lchanadigan mahsulotlar; "
            "nomi Uzum'dagi kamida 3 ta mahsulotga mos kelganlari ko'rsatiladi (20 baravardan ortiq farq — boshqa "
            "mahsulot deb hisoblanadi va chiqarilmaydi). «Uzum narxi» ustiga bosing — "
            "Uzum'dagi qidiruv ochiladi; «Tender» / «Uzum» tugmalari — lot asl saytda yoki mahsulot Uzum'da ochiladi; "
            "qatorning boshqa joyini bosing — lotdagi <b>hamma mahsulotlar</b> ro'yxati ochiladi. "
            "<span class=\"muted\">Uzum chakana narxi; tenderda yetkazib berish, kafolat va QQS ham bo'lishi mumkin. "
            "Uzum'da solishtirib bo'lmaydigan mahsulotlar (kg, litr…) narxini AI taxmin qiladi — «AI» belgisi bilan, "
            "lot sahifasida.</span>")
    return layout(ctx, "uzum", f"""{dir_chips(ctx, "/all/uzum", yon, dir_counts, f=f, q=q, sm=sm, zk=zk, av=av)}
{money_chips(ctx, "/all/uzum", {"sm": sm, "zk": zk, "av": av}, band_counts, f=f, q=q, y=yon)}
<div class="stats">{stats}</div>{help_box(info)}<div class="seg" style="margin:0 0 12px">{ftabs}</div>
{filter_bar(ctx, "/all/uzum", inner, xlsx=xl)}{note}{table}""", title="Uzum narxlari bilan solishtirish",
                  sub="Tovar tenderlaridagi narx ↔ Uzum Market").replace(
        "<title>", '<meta http-equiv="refresh" content="900"><title>', 1)


AI_PRICE_CLS = {"QIMMAT": "bad", "ME'YORIDA": "good", "ARZON": "good", "NOANIQ": "maybe"}
AI_SUPPLY_CLS = {"QATNASHISH MUMKIN": "good", "SHARTLI": "maybe", "TAVSIYA ETILMAYDI": "bad"}


def _lot_info(lot, d: dict) -> str:
    def dt(v):
        return fmt_local(datetime.fromisoformat(v)) if v else None

    cur = d.get("cur") or lot.currency
    cost = d.get("cost") or lot.start_cost
    deadline = lot.deadline_at
    rows = [
        ("Lot raqami", f'<span class="lotno">{ext_link(lot.url, lot.external_id)}</span> · '
                       f'{h(tc.SOURCE_NAME.get(lot.source, lot.source))}' + (f' · {h(d["type"])}' if d.get("type") else "")),
        ("Buyurtmachi", h(d.get("customer") or lot.customer_name or "noma'lum")),
        ("INN / viloyat", h(f"{d.get('tin') or '?'} · {d.get('region') or getattr(lot, 'customer_region', None) or '?'}")),
        ("Boshlang'ich narx", f"<b>{h(tc.money_uzs(cost, cur))}</b>" if cost else "noma'lum"),
        ("E'lon qilingan", h(dt(d.get("published")) or "noma'lum")),
        ("Topshirish muddati", h(f"{fmt_local(deadline)} · {remaining_uz(deadline)}") if deadline else "noma'lum"),
        ("Zakalat", h(f"{d['pledge_pct']:g}%" + (f" = {tc.money_uzs(d['pledge'], cur)}" if d.get("pledge") else ""))
         if d.get("pledge_pct") else "talab qilinmaydi / noma'lum"),
    ]
    terms = [f"avans {d['advance_pct']:g}%" if d.get("advance_pct") is not None else None,
             f"to'lov {d['pay_days']} kun" if d.get("pay_days") else None,
             f"yetkazish {d['exec_days']} kun" if d.get("exec_days") else None,
             f"kafolat {d['warranty']}" if d.get("warranty") else None]
    rows.append(("Shartlar", h(", ".join(t for t in terms if t) or "noma'lum")))
    if d.get("valuation"):
        rows.append(("Baholash usuli", h(d["valuation"])))
    if d.get("financing") or d.get("languages"):
        rows.append(("Moliya / til", h(f"{d.get('financing') or '?'} · {', '.join(d.get('languages') or []) or '?'}")))
    if d.get("status"):
        rows.append(("Saytdagi holat", h(d["status"])))
    kv = "".join(f"<dt>{h(k)}</dt><dd>{v}</dd>" for k, v in rows)
    out = f'<h2>Lot ma\'lumotlari</h2><div class="panel"><dl class="kv">{kv}</dl></div>'
    if not d:
        out += ('<p class="sub muted">Lot kartasini saytdan o\'qib bo\'lmadi — faqat ro\'yxatdagi ma\'lumotlar '
                "ko'rsatilgan.</p>")
    if d.get("description"):
        out += (f'<details class="panel"><summary><b>Tavsif</b></summary>'
                f'<p style="white-space:pre-wrap;margin:10px 0 0">{h(d["description"])}</p></details>')
    if d.get("qualification"):
        out += ("<details class=\"panel\"><summary><b>Malaka talablari</b> "
                f"<span class=\"muted\">({len(d['qualification'])})</span></summary><ul>"
                + "".join(f"<li>{h(x)}</li>" for x in d["qualification"]) + "</ul></details>")
    if d.get("files"):
        out += ("<details class=\"panel\"><summary><b>Fayllar</b> <span class=\"muted\">(asl saytda, "
                f"{len(d['files'])})</span></summary><ul>"
                + "".join(f"<li>{h(f.get('label') or '')}{(' — ' + h(f['name'])) if f.get('name') else ''}</li>"
                          for f in d["files"]) + "</ul></details>")
    return out


def _ai_block(ctx: Ctx, lp, ai: dict | None, busy: bool) -> str:
    form = (f'<form method="post" action="{h(ctx.url(f"/all/uzum/lot/{lp.id}/ai"))}" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{h(ctx.csrf or "")}">'
            f'<input type="hidden" name="back" value="{h(ctx.url(f"/all/uzum/lot/{lp.id}"))}">'
            f'<button class="ghost" type="submit">{icon("refresh")}Qayta tahlil qilish</button></form>')
    head = f'<h2 id="ai">{icon("bot")}AI xulosasi'
    if ai is None and busy:
        return (f'{head}</h2><div class="panel"><b>AI lotni tahlil qilmoqda…</b> '
                '<span class="muted">10–30 soniya; sahifa o\'zi yangilanadi.</span></div>')
    if ai is None:
        return f'{head}</h2><div class="panel muted">AI o\'chirilgan (config: tenders.prices.ai_enabled).</div>'
    note = ('<p class="muted" style="margin:8px 0 0">Yangi ma\'lumotlar bilan qayta tahlil qilinmoqda — sahifa o\'zi '
            "yangilanadi.</p>") if busy else ""
    if ai.get("error"):
        return (f'{head}</h2><div class="panel"><p style="margin-top:0">AI xulosa tayyorlay olmadi: '
                f'<span class="muted">{h(ai["error"])}</span></p>{"" if busy else form}{note}</div>')
    pv, sv = ai.get("price_verdict", "NOANIQ"), ai.get("supplier_verdict", "SHARTLI")
    badges = (f' <span class="pill {AI_PRICE_CLS.get(pv, "")}">Narx: {h(pv)}</span>'
              f' <span class="pill {AI_SUPPLY_CLS.get(sv, "")}">{h(sv)}</span>')
    m = ai.get("est_margin_percent") or []
    margin = (f"<p><b>Taxminiy ustama:</b> {m[0]}–{m[1]}% <span class=\"muted\">(Uzum narxida olib, tender narxida "
              "yetkazilsa; QQS va yetkazishdan oldin)</span></p>") if len(m) == 2 else ""

    def bullets(title: str, items) -> str:
        items = [x for x in (items or []) if x]
        return (f"<p style=\"margin:12px 0 4px\"><b>{h(title)}</b></p><ul style=\"margin:0\">"
                + "".join(f"<li>{h(x)}</li>" for x in items) + "</ul>") if items else ""

    notes = [f"{n['name']} — {n['note']}" for n in ai.get("item_notes") or []]
    at = ai.get("_at")
    foot = (f'<p class="muted" style="margin:12px 0 0;font-size:12px">{h(ai.get("_model") or "")} · '
            f'{h(fmt_local(datetime.fromisoformat(at)) if at else "")} · AI xulosa taxminiy — Uzum havolalari '
            f'bilan tekshiring. {"" if busy else form}</p>')
    return (f'{head}{badges}</h2><div class="panel">'
            f'<p style="margin-top:0">{h(ai.get("summary_uz") or "")}</p>'
            f'<p><b>Narxlar:</b> {h(ai.get("price_comment_uz") or "")}</p>'
            f'<p><b>Yetkazib beruvchi uchun:</b> {h(ai.get("supplier_reason_uz") or "")}</p>{margin}'
            + bullets("Muhim shartlar", ai.get("key_conditions"))
            + bullets("Shubhali joylar", ai.get("red_flags"))
            + bullets("Risklar", ai.get("risks"))
            + bullets("Mahsulotlar bo'yicha", notes)
            + bullets("Buyurtmachiga savollar", ai.get("questions_for_customer_uz"))
            + f"{note}{foot}</div>")


def _item_cards(rows) -> str:
    out = []
    for r in rows:
        it = r.item
        lines = [("Miqdori", f"{it.qty:g} {it.unit or ''}".strip() if it.qty else "noma'lum"),
                 ("Tender narxi", f"{tc.money_uzs(r.price_uzs)} × {it.qty or 1:g} = "
                                  f"{tc.money_uzs(r.price_uzs * (it.qty or 1))}")]
        if it.code:
            lines.append(("Mahsulot kodi", it.code))
        lines.append(("Holat", r.status_text))
        if r.status == "ai":
            lines.append(("AI bozor narxi", f"{tc.money_uzs(r.uzum_med)} — {r.note}"))
        elif r.uzum_med:
            lines.append(("Uzum narxi", f"o'rtacha {tc.money_uzs(r.uzum_med)} · eng arzoni {tc.money_uzs(r.uzum_min)} "
                                        f"· {r.n} ta mos mahsulot"))
        if r.ratio:
            lines.append(("Farq", f"tender ×{r.ratio:.1f} qimmat" if r.ratio >= 1
                          else f"tender ×{1 / r.ratio:.1f} arzon"))
            lines.append(("Farq summasi", tc.money_uzs(r.overpay) if r.overpay >= 0
                          else f"−{tc.money_uzs(-r.overpay)}"))
        lines.append(("Uzum'da qidirildi", f"«{r.query}»" + (f" · {fmt_local(r.checked_at)}" if r.checked_at else "")))
        kv = "".join(f"<dt>{h(k)}</dt><dd>{h(v)}</dd>" for k, v in lines)
        if r.best:
            kv += f"<dt>Uzum'dagi misol</dt><dd>{ext_link(r.best.url, r.best.title)}</dd>"
        btn = ext_btn(r.lot.url, "doc", "Tender") + (
            " " + ext_btn(r.uzum_url, "cart", "Uzum") if r.status != "unit" else "")
        out.append(f'<div class="panel item" id="i-{r.idx}"><p style="margin:0 0 10px"><b>{h(it.name)}</b> '
                   f'{btn}</p><dl class="kv">{kv}</dl></div>')
    return "".join(out)


def _lot_totals(ok, tender_sum: float, uzum_sum: float) -> dict[str, str]:
    """Totals row under the lot's item table: compared items × quantity, and the net gap (+ dearer / − cheaper)."""
    net = tender_sum - uzum_sum
    ratio = tender_sum / uzum_sum if uzum_sum else 0
    if net >= 0:
        gap = f'<b style="color:var(--bad)">+{h(tc.money_uzs(net))}</b>'
        side = f'<span class="pill bad">tender ×{ratio:.1f} qimmat</span>' if ratio else ""
        word = "tender qimmat (Uzum arzon)"
    else:
        gap = f'<b style="color:var(--good)">−{h(tc.money_uzs(-net))}</b>'
        side = f'<span class="pill good">tender ×{1 / ratio:.1f} arzon</span>' if ratio else ""
        word = "tender arzon (Uzum qimmat)"
    n_ai = sum(1 for r in ok if r.status == "ai")
    return {"name": f"Jami: {len(ok)} ta mahsulot <span class=\"muted\">(narx × miqdor · Uzum {len(ok) - n_ai}, "
                    f"AI {n_ai})</span>",
            "tender": h(tc.money_uzs(tender_sum)), "uzum": h(tc.money_uzs(uzum_sum)), "ratio": side,
            "over": gap, "status": h(word)}


def price_lot_page(ctx: Ctx, lp, *, big: float, detail: dict, ai: dict | None, ai_busy: bool) -> str:
    """One goods lot: card details, AI summary, every item with its Uzum check."""
    from app.web.tables import price_cols, table_html

    lot = lp.lot
    cols = [c for c in price_cols(big) if c.key not in ("no", "deadline", "customer", "lprice", "pledge", "verdict",
                                                          "win")]
    order = {"ok": 0, "far": 1, "nomatch": 2, "pending": 3}
    rows = sorted(lp.rows, key=lambda r: (order.get(r.status, 4), -r.ratio, r.idx))
    ok = lp.compared
    tender_sum = sum(r.price_uzs * (r.item.qty or 1) for r in ok)
    uzum_sum = sum(r.uzum_med * (r.item.qty or 1) for r in ok)
    table = table_html(ctx, f"/all/uzum/lot/{lot.id}", rows, cols, sort="", direction="desc", query={},
                       detail=lambda r: f"#i-{r.idx}", bulk=[], noun="mahsulot",
                       foot=_lot_totals(ok, tender_sum, uzum_sum) if ok else None)
    n_ai = sum(1 for r in ok if r.status == "ai")
    stats = (stat("Lotdagi mahsulotlar", len(lp.rows), "hammasi quyida", blue=True)
             + stat("Narxi hisoblandi", f"{len(ok)} / {len(lp.rows)}", f"Uzum: {len(ok) - n_ai} · AI taxmini: {n_ai}")
             + stat("Hisoblanganlar: tenderda", tc.money_uzs(tender_sum), f"bozor narxida {tc.money_uzs(uzum_sum)}")
             + stat("Ortiqcha summa", tc.money_uzs(max(lp.overpay(big), 0)), f"Uzum ≥{big:g}× arzon bo'lganlar"))
    head = (f'<div class="panel"><b style="font-size:15px">{h(detail.get("title") or lot.title)}</b>'
            f'<p style="margin:10px 0 0;display:flex;gap:6px;flex-wrap:wrap">{ext_btn(lot.url, "doc", "Lotni tender saytida ochish")} '
            f'<a class="btn ghost xs" href="#ai">{icon("bot")}AI xulosasi</a> {chat_button(ctx, lot.id)} '
            f'<a class="btn ghost xs" href="#items">{icon("cart")}Mahsulotlar</a> '
            f'<a class="btn link xs" href="{h(ctx.url("/all/uzum", f="lots"))}">← Lotlar</a></p></div>')
    note = ('<p class="sub">«Tender» — lot asl saytda ochiladi, «Uzum» — mahsulot Uzum\'da qidiriladi. '
            "Qatorning boshqa joyini bossangiz — shu mahsulot haqida batafsil ma'lumot (pastda) ochiladi. "
            "«Navbatda» bo'lganlarini telefon keyingi tekshiruvlarda qidiradi. Uzum'da solishtirib bo'lmagan "
            "mahsulotlar (kg/litr/metr, Uzum'da yo'q) narxini AI bozor narxi bo'yicha taxmin qiladi — «AI» belgisi.</p>")
    body = (f'{head}<div class="stats">{stats}</div>{_ai_block(ctx, lp, ai, ai_busy)}{_lot_info(lot, detail)}'
            f'<h2 id="items">Mahsulotlar va Uzum narxlari</h2>{note}{table}'
            f'<h2>Mahsulotlar batafsil</h2>{_item_cards(rows)}')
    page = layout(ctx, "goods", body, title=f"Lot {lot.external_id}", sub="Lot haqida batafsil · AI xulosasi · "
                  "Uzum narxlari")
    if ai_busy:
        page = page.replace("<title>", '<meta http-equiv="refresh" content="8"><title>', 1)
    return page


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
        f'<span class="sw {k}"></span>{h(label)}{icon("check") if ctx.theme == k else ""}</button>'
        for k, label in THEMES.items())
    tiles = "".join(f'<div class="stat"><span>{h(label)}</span><b>{n}</b></div>' for label, n in stats)
    body = f"""<div class="panel prof"><div class="avatar">{h(user[:1].upper() or "A")}</div>
<div><h1>{h(user)}</h1><p class="sub" style="margin:0">Sayt egasi · sessiya {h(session_until)} gacha</p></div></div>
<h2>Ko'rinish</h2>
<form class="themes" method="post" action="{h(ctx.url("/theme"))}">
<input type="hidden" name="csrf" value="{h(ctx.csrf)}"><input type="hidden" name="back" value="{h(ctx.url("/profile"))}">
{opts}</form>
<p class="muted">"Avtomatik" — telefon yoki kompyuter sozlamasiga qarab o'zi qora/oq bo'ladi. Chap paneldagi
oy/quyosh tugmasi ham istalgan paytda almashtiradi.</p>
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
                href, text, external = url, b.get("text") or "Havola", True
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


# ── 🤖 AI chat about one lot (app/tenders/chat.py) ──────────────────────────
_ICON["info"] = '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>'
_ICON["doc"] = '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'
_ICON["refresh"] = '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4"/>'
_ICON["check"] = '<path d="m5 12.5 4.5 4.5L19 7.5"/>'
_ICON["bot"] = ('<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V4.5M9 13h.01M15 13h.01'
                'M9.5 16.2c1.5.9 3.5.9 5 0"/><circle cx="12" cy="3.5" r="1"/>')
_ICON["send"] = '<path d="M4 12 20 4l-6 16-2.5-6.5z"/><path d="M11.5 13.5 20 4"/>'

CSS += """
.chatwrap{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start}
.chatlog{display:flex;flex-direction:column;gap:12px;padding:4px 0 8px}
.msg{max-width:min(760px,92%);padding:11px 15px;border-radius:14px;line-height:1.55;font-size:14px;
  overflow-wrap:anywhere;border:1px solid var(--line)}
.msg.user{align-self:flex-end;background:var(--brand);color:var(--on-ink);border-color:transparent;
  border-bottom-right-radius:4px;white-space:pre-wrap}
.msg.ai{align-self:flex-start;background:var(--card);border-bottom-left-radius:4px}
.msg.ai p{margin:0 0 8px}.msg.ai p:last-child{margin:0}.msg.ai ul,.msg.ai ol{margin:4px 0 8px;padding-left:20px}
.msg.ai li{margin:2px 0}.msg.ai hr{border:0;border-top:1px solid var(--line);margin:12px 0}
.msg.ai blockquote{margin:8px 0;padding:9px 12px;background:var(--brand-bg);border-radius:8px}.msg.ai code{font-family:var(--mono);font-size:12.5px}.msg .when{display:block;font-size:11px;opacity:.6;margin-top:6px}
.msg.err{background:var(--bad-bg);color:var(--bad);border-color:var(--bad-line)}
.msg.wait{color:var(--muted)}.msg.wait::after{content:"";display:inline-block;width:1.2em;text-align:left;
  animation:dots 1.2s steps(4) infinite}
@keyframes dots{0%{content:""}25%{content:"."}50%{content:".."}75%{content:"..."}}
@media (prefers-reduced-motion:reduce){.msg.wait::after{animation:none;content:"…"}}
form.ask{position:sticky;bottom:0;display:flex;gap:8px;align-items:flex-end;padding:12px 0 4px;
  background:linear-gradient(transparent,var(--bg) 30%)}
form.ask textarea{flex:1;min-height:46px;max-height:200px;resize:vertical;padding:11px 13px;border-radius:12px;
  font:inherit;line-height:1.4}
form.ask button{height:46px;border-radius:12px;padding:0 16px}
.sugs{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 6px}
.sugs button{height:auto;padding:6px 11px;border-radius:999px;font-size:12.5px;background:var(--card);color:var(--ink);
  border:1px solid var(--line);font-weight:500}
.sugs button:hover{border-color:var(--brand);color:var(--brand);background:var(--brand-bg)}
.chatside{position:sticky;top:64px}.chatside .panel{font-size:13px}.chatside dl.kv{grid-template-columns:1fr;gap:2px 0}
.chatside dl.kv dt{margin-top:8px}.chatside ul.docs{margin:6px 0 0;padding-left:16px}
.chatside ul.docs li{margin:3px 0}
.newchat{display:flex;gap:8px;flex-wrap:wrap}.newchat input{flex:1;min-width:260px;height:42px}
.newchat button{height:42px}
.chatlist a.row{display:flex;gap:12px;align-items:center;padding:11px 4px;border-top:1px solid var(--line);
  color:var(--ink);text-decoration:none}
.chatlist a.row:first-child{border-top:0}.chatlist a.row:hover{background:var(--hover)}
.chatlist .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.loading{display:flex;gap:12px;align-items:center}
.spin{width:18px;height:18px;border:2px solid var(--line2);border-top-color:var(--brand);border-radius:50%;
  animation:spin .8s linear infinite;flex:none}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation:none}}
@media (max-width:980px){.chatwrap{grid-template-columns:1fr}.chatside{order:-1;position:static}}
"""


def _md(text: str) -> str:
    """Gemini's answer → safe HTML: escaped first, then **bold**, `code`, '- ' / '1. ' lists and paragraphs."""
    out: list[str] = []
    lst: str | None = None

    def inline(s: str) -> str:
        s = h(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)

    def close() -> None:
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = None

    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()

    quote: list[str] = []

    def flush_quote() -> None:
        if quote:
            out.append("<blockquote>" + _md("\n".join(quote)) + "</blockquote>")
            quote.clear()

    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        if re.match(r"^\s*>\s?", line):
            flush()
            close()
            quote.append(re.sub(r"^\s*>\s?", "", line))
            continue
        flush_quote()
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush()
            close()
            out.append("<hr>")
            continue
        m_ul = re.match(r"^\s*[-*•]\s+(.*)", line)
        m_ol = re.match(r"^\s*\d+[.)]\s+(.*)", line)
        m_h = re.match(r"^\s*#{1,6}\s+(.*)", line)
        if m_ul or m_ol:
            flush()
            kind = "ul" if m_ul else "ol"
            if lst != kind:
                close()
                out.append(f"<{kind}>")
                lst = kind
            out.append(f"<li>{inline((m_ul or m_ol).group(1))}</li>")
        elif not line.strip():
            flush()
            close()
        elif m_h:
            flush()
            close()
            out.append(f"<p><b>{inline(m_h.group(1))}</b></p>")
        else:
            close()
            para.append(line)
    flush()
    close()
    flush_quote()
    return "".join(out)


def chat_message(m: dict) -> str:
    when = ""
    if m.get("at"):
        with contextlib.suppress(ValueError):
            when = f'<span class="when">{h(fmt_local(datetime.fromisoformat(m["at"])))}</span>'
    if m.get("role") == "user":
        return f'<div class="msg user">{h(m.get("text"))}{when}</div>'
    return f'<div class="msg ai">{_md(m.get("text", ""))}{when}</div>'


def chat_index_page(ctx: Ctx, recent: list[dict], it_lots: list, *, value: str = "", error: str = "") -> str:
    from app.tenders.chat import SOURCE_TITLE

    form = (f'<form class="newchat" method="post" action="{h(ctx.url("/chat"))}">'
            f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
            f'<input type="url" name="url" value="{h(value)}" required autofocus inputmode="url" '
            f'placeholder="Lot havolasi: https://etender.uzex.uz/lot/527202" aria-label="Lot havolasi">'
            f'<button type="submit">{icon("bot")}Lotni ochish</button></form>')
    err = f'<div class="flash err" style="margin-top:12px">{h(error)}</div>' if error else ""
    help_txt = ("<p class=\"muted\" style=\"margin:10px 0 0;font-size:13px\">Ishlaydigan saytlar: "
                + " · ".join(h(v) for v in SOURCE_TITLE.values())
                + ". Bot lot sahifasini, pozitsiyalarni va fayllarni (TZ, shartnoma loyihasi) o'qiydi, keyin "
                  "savollaringizga shu hujjatlar asosida javob beradi. Telefon orqali o'qiladigan saytlarda "
                  "(auksion, Hayot birja) lot 30–60 soniyada ochiladi.</p>")
    rows = ""
    for c in recent:
        n = sum(1 for m in c.get("messages", []) if m.get("role") == "user")
        st = {"loading": '<span class="pill maybe">o\'qilmoqda</span>',
              "error": '<span class="pill bad">xato</span>'}.get(c.get("status"), f'<span class="chip">{n} savol</span>')
        rows += (f'<a class="row" href="{h(ctx.url("/chat/" + c["key"]))}">{icon("bot")}'
                 f'<span class="t"><b>{h(c.get("title") or c["key"])}</b><br><span class="muted">'
                 f'{h(SOURCE_TITLE.get(c.get("source"), c.get("source")))} · {h(c.get("customer") or "")}</span></span>'
                 f'{st}</a>')
    chats = (f'<h2>Suhbatlar</h2><div class="panel chatlist">{rows}</div>' if rows else "")
    quick = ""
    if it_lots:
        quick = ('<h2>Ochiq IT lotlar — bir bosishda so\'rash</h2><div class="panel chatlist">' + "".join(
            f'<a class="row" href="{h(ctx.url("/chat/new", ref=f"{lot.source}-{lot.external_id}"))}">{icon("tenders")}'
            f'<span class="t"><b>{h(lot.title)}</b><br><span class="muted">{h(tc.SOURCE_NAME.get(lot.source, lot.source))}'
            f' · {h(lot.customer_name or "")} · muddat {h(fmt_local(lot.deadline_at))}</span></span>'
            f'<span class="chip">so\'rash →</span></a>' for lot in it_lots) + "</div>")
    body = f'<div class="panel">{form}{help_txt}{err}</div>{chats}{quick}'
    return layout(ctx, "chat", body, title="AI chat", sub="Istalgan lot haqida savol bering — AI hujjatlarini o'qib javob beradi")


def chat_page(ctx: Ctx, key: str, chat: dict) -> str:
    from app.tenders.chat import SOURCE_TITLE, SUGGESTIONS, deadline_text

    status = chat.get("status")
    msgs = "".join(chat_message(m) for m in chat.get("messages", []))
    if status == "loading":
        main = ('<div class="panel loading"><span class="spin"></span><div><b>Lot o\'qilmoqda…</b><br>'
                '<span class="muted">Sahifa, pozitsiyalar va fayllar o\'qilyapti, skanerlar AI bilan matnga '
                'aylantirilyapti. Bu 10 soniyadan 2 daqiqagacha oladi — sahifa o\'zi yangilanadi.</span></div></div>')
    elif status == "error":
        main = (f'<div class="flash err">Lotni o\'qib bo\'lmadi: {h(chat.get("error"))}</div>'
                f'<form method="post" action="{h(ctx.url("/chat"))}"><input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
                f'<input type="hidden" name="url" value="{h(key)}"><input type="hidden" name="fresh" value="1">'
                f'<button type="submit">Qayta urinish</button></form>')
    else:
        empty = ('' if msgs else '<div class="msg ai">Lot o\'qildi. Savolingizni yozing yoki quyidagilardan birini tanlang.</div>')
        sugs = "".join(f'<button type="submit" name="q" value="{h(s)}" formnovalidate>{h(s)}</button>' for s in SUGGESTIONS)
        main = (f'<div class="chatlog" id="chatlog">{msgs}{empty}</div>'
                f'<form class="ask" method="post" action="{h(ctx.url(f"/chat/{key}/ask"))}" data-chat>'
                f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
                f'<textarea name="q" rows="1" maxlength="2000" required placeholder="Lot haqida savol yozing…" '
                f'title="Enter — yuborish, Shift+Enter — yangi qator" aria-label="Savol"></textarea>'
                f'<button type="submit" aria-label="Yuborish">{icon("send")}</button></form>'
                f'<form method="post" action="{h(ctx.url(f"/chat/{key}/ask"))}" data-chat class="sugs">'
                f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">{sugs}</form>')
    docs = chat.get("docs") or []
    doc_li = "".join(
        f'<li>{h(d.get("label"))}: {h(d.get("file"))} <span class="muted">— '
        f'{h(d.get("note") or (str(d.get("chars")) + " belgi"))}</span></li>' for d in docs)
    cost = tc.money_uzs(chat.get("cost"), chat.get("currency") or "UZS") if chat.get("cost") else "noma'lum"
    customer = chat.get("customer") or "noma'lum"
    side = (f'<div class="chatside"><div class="panel"><b>{h(chat.get("title") or key)}</b>'
            f'<dl class="kv"><dt class="muted">Manba</dt><dd>{h(SOURCE_TITLE.get(chat.get("source"), chat.get("source")))}'
            f'{(" · " + h(chat.get("lot_type"))) if chat.get("lot_type") else ""}</dd>'
            f'<dt class="muted">Buyurtmachi</dt><dd>{h(customer)}</dd>'
            f'<dt class="muted">Boshlang\'ich narx</dt><dd><b>{h(cost)}</b></dd>'
            f'<dt class="muted">Topshirish muddati</dt><dd>{h(deadline_text(chat))}</dd>'
            f'<dt class="muted">Saytdagi holati</dt><dd>{h(chat.get("status_text") or "—")}</dd>'
            f'<dt class="muted">AI o\'qigan hujjatlar</dt><dd>'
            + (f'<ul class="docs">{doc_li}</ul>' if doc_li else "faqat lot sahifasi" + (
                f' va {chat.get("items")} ta pozitsiya' if chat.get("items") else "")) + "</dd></dl>"
            f'<p style="margin:12px 0 0;display:flex;gap:6px;flex-wrap:wrap">'
            f'{ext_btn(chat.get("url"), "doc", "Saytda ochish")}'
            f'<a class="btn link xs" href="{h(ctx.url("/chat"))}">← Suhbatlar</a></p>'
            + (f'<form method="post" action="{h(ctx.url(f"/chat/{key}/clear"))}" style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">'
               f'<input type="hidden" name="csrf" value="{h(ctx.csrf)}">'
               f'<button class="ghost xs btn" type="submit" name="what" value="clear">Suhbatni tozalash</button>'
               f'<button class="ghost xs btn" type="submit" name="what" value="reload">Lotni qayta o\'qish</button></form>'
               if status == "ready" else "") + "</div></div>")
    body = f'<div class="chatwrap"><section>{main}</section>{side}</div>'
    page = layout(ctx, "chat", body, title="AI chat", sub=chat.get("title") or key)
    if status == "loading":
        page = page.replace("<title>", '<meta http-equiv="refresh" content="4"><title>', 1)
    return page


def chat_button(ctx: Ctx, key: str, cls: str = "btn ghost xs") -> str:
    return f'<a class="{cls}" href="{h(ctx.url("/chat/new", ref=key))}">{icon("bot")}AI\'dan so\'rash</a>'


def ext_btn(url: str | None, ic: str, text: str, cls: str = "btn ghost xs") -> str:
    """ext_link with a drawn icon in front (no emoji in the UI)."""
    u = safe_url(url)
    if not u:
        return h(text)
    return (f'<a class="{cls}" href="{h(u)}" target="_blank" rel="noopener noreferrer nofollow">'
            f'{icon(ic)}{h(text)}</a>')
