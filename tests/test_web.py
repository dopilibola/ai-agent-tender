import re
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr
from sqlalchemy import select

from app.core.states import JobStatus
from app.db.models import Job, Lot, LotStatus, Proposal
from app.pipeline.service import FreelanceService
from app.tenders.service import TenderService
from app.web.server import build_app

PASSWORD = "s3cret-pass"


class Sender:
    def __init__(self):
        self.messages = []

    async def send(self, text, reply_markup=None, **kw):
        self.messages.append(text)

    async def send_text(self, text):
        self.messages.append(text)


@pytest.fixture
async def client(settings, sessionmaker):
    settings.secrets.web_admin_password = SecretStr(PASSWORD)
    settings.config.web.base_path = "/tender"
    sender = Sender()
    tenders = TenderService(settings, sessionmaker, {}, sender)
    freelance = FreelanceService(settings, sessionmaker, {}, gemini=None, sender=sender)
    future = datetime.now(UTC) + timedelta(days=5)
    async with sessionmaker() as s:
        s.add_all([
            Lot(source="etender", external_id="525968", url="https://etender.uzex.uz/lot/525968",
                title='<script>alert(1)</script> Dasturiy ta\'minot', customer_name="Vazirlik", start_cost=2e8,
                deadline_at=future, status=LotStatus.NOTIFIED, verdict="QATNASHAMIZ",
                analysis={"fit_score": 82, "summary_uz": "Veb-portal ishlab chiqish"}),
            Lot(source="xarid", external_id="777", url="javascript:alert(1)", title="Server", status=LotStatus.NOTIFIED,
                deadline_at=future),
        ])
        job = Job(platform="kwork", external_id="k1", url="https://kwork.ru/projects/1", title="Telegram bot kerak",
                  status=JobStatus.AWAITING_APPROVAL, fit=85)
        s.add(job)
        await s.flush()
        s.add(Proposal(job_id=job.id, text="MAXFIY TAKLIF MATNI", price=100, currency="USD"))
        await s.commit()
    app = build_app(settings, sessionmaker, tenders=tenders, freelance=freelance, notify=sender.send_text)
    async with TestClient(TestServer(app)) as c:
        c.sender = sender
        yield c


async def login(client) -> None:
    r = await client.post("/tender/login", data={"user": "admin", "password": PASSWORD, "next": "/tender/"}, allow_redirects=False)
    assert r.status == 303


async def csrf(client, path="/tender/") -> str:
    html = await (await client.get(path)).text()
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


async def test_everything_needs_login(client):
    for path in ("/tender/", "/tender/lots", "/tender/jobs", "/tender/lot/1", "/tender/job/1"):
        r = await client.get(path, allow_redirects=False)
        assert r.status == 303 and r.headers["Location"].startswith("/tender/login"), path
    html = await (await client.get("/tender/login")).text()
    assert 'name="user"' in html and 'name="password"' in html
    assert "Telegram xabarlari</a>" not in html  # no navigation before login
    assert (await client.get("/tender/healthz")).status == 200


async def test_list_has_links_and_escapes(client):
    await login(client)
    r = await client.get("/tender/lots")
    html = await r.text()
    assert r.status == 200
    assert 'href="https://etender.uzex.uz/lot/525968"' in html
    assert "<script>alert(1)" not in html
    assert "javascript:" not in html  # unsafe URLs are not linked
    assert r.headers["Content-Security-Policy"].startswith("default-src 'none'")


async def test_lot_detail_and_404(client, sessionmaker):
    await login(client)
    async with sessionmaker() as s:
        lot_id = (await s.scalar(select(Lot.id).where(Lot.external_id == "525968")))
    assert "saytda ochish" in await (await client.get(f"/tender/lot/{lot_id}")).text()
    assert (await client.get("/tender/lot/99999")).status == 404
    assert (await client.get("/tender/lot/abc")).status == 404


async def test_jobs_show_links_and_proposal(client, sessionmaker):
    await login(client)
    html = await (await client.get("/tender/jobs")).text()
    assert 'href="https://kwork.ru/projects/1"' in html
    async with sessionmaker() as s:
        job_id = await s.scalar(select(Job.id))
    assert "MAXFIY TAKLIF MATNI" in await (await client.get(f"/tender/job/{job_id}")).text()


async def test_actions_need_login_and_csrf(client, sessionmaker):
    async with sessionmaker() as s:
        lot_id = await s.scalar(select(Lot.id).where(Lot.external_id == "525968"))
    r = await client.post(f"/tender/lot/{lot_id}/bid", data={}, allow_redirects=False)
    assert r.status == 303 and r.headers["Location"].startswith("/tender/login")
    await login(client)
    r = await client.post(f"/tender/lot/{lot_id}/bid", data={"csrf": "bad"}, allow_redirects=False)
    assert r.status == 403
    token = await csrf(client)
    r = await client.post(f"/tender/lot/{lot_id}/bid", data={"csrf": token, "back": "https://evil.com/"},
                          allow_redirects=False)
    assert r.status == 303 and r.headers["Location"] == "/tender/"
    async with sessionmaker() as s:
        assert (await s.get(Lot, lot_id)).status == LotStatus.BIDDING
    assert any("525968" in m for m in client.sender.messages)


async def test_wrong_password_is_rate_limited(client):
    for _ in range(5):
        r = await client.post("/tender/login", data={"user": "admin", "password": "nope"}, allow_redirects=False)
        assert r.status == 403
    r = await client.post("/tender/login", data={"user": "admin", "password": PASSWORD}, allow_redirects=False)
    assert r.status == 429


async def test_feed_mirrors_telegram(client, sessionmaker):
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    from app.bot.feed import FeedSender
    from app.tenders.cards import TenderAction

    class Tg:
        def __init__(self):
            self.sent = []

        async def send(self, text, reply_markup=None, **kw):
            self.sent.append(text)

    tg = Tg()
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 To'liq", callback_data=TenderAction(action="full", lot_id=7))
    kb.button(text="🔗 Saytda ochish", url="https://etender.uzex.uz/lot/525968")
    await FeedSender(tg, sessionmaker, "tender").send(
        '<b>🆕 Yangi IT lot</b>\n🔗 <a href="https://etender.uzex.uz/lot/525968">Saytda</a> <img src=x onerror=alert(1)>',
        kb.as_markup())
    await FeedSender(tg, sessionmaker, "email").send_text("📧 <b>Upwork</b>: MAXFIY OFFER")
    assert len(tg.sent) == 2

    await login(client)
    html = await (await client.get("/tender/")).text()
    assert "<b>🆕 Yangi IT lot</b>" in html
    assert 'href="https://etender.uzex.uz/lot/525968"' in html
    assert 'href="/tender/lot/7"' in html  # callback button → site page
    assert "<img" not in html and "onerror" not in html
    assert "MAXFIY OFFER" in html  # every Telegram message is shown after login


def test_tg_html_sanitizer():
    from app.web.tghtml import to_web

    assert to_web("<b>a</b> & <i>b") == "<b>a</b> &amp; <i>b</i>"
    assert to_web('<a href="javascript:x">y</a>') == "<a>y</a>"
    assert to_web('<script>alert(1)</script><b onclick="x">z</b>') == "alert(1)<b>z</b>"


async def test_wrong_username_rejected(client):
    r = await client.post("/tender/login", data={"user": "root", "password": PASSWORD}, allow_redirects=False)
    assert r.status == 403


async def test_lots_table_sorting_and_ai_column(client, sessionmaker):
    await login(client)
    html = await (await client.get("/tender/lots?sort=fit&dir=desc")).text()
    assert '<table class="grid">' in html and "AI izohi" in html
    assert '<th class="num on">' in html  # current sort column is highlighted, next click flips direction
    assert "sort=fit&amp;dir=asc" in html
    # lot with fit 82 first, the one without analysis last
    assert html.index("525968") < html.index(">777<")
    html = await (await client.get("/tender/lots?sort=fit&dir=asc")).text()
    assert html.index("525968") < html.index(">777<")  # empty values stay at the bottom
    assert (await client.get("/tender/lots?sort=bogus&dir=evil")).status == 200


async def test_excel_export(client):
    import io
    import zipfile
    from xml.dom.minidom import parseString

    await login(client)
    for path in ("/tender/lots.xlsx?sort=price&dir=desc", "/tender/jobs.xlsx"):
        r = await client.get(path)
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("application/vnd.openxmlformats")
        z = zipfile.ZipFile(io.BytesIO(await r.read()))
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                parseString(z.read(name))  # every part is well-formed XML
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        assert "AI" in sheet
    r = await client.get("/tender/lots.xlsx")
    sheet = zipfile.ZipFile(io.BytesIO(await r.read())).read("xl/worksheets/sheet1.xml").decode()
    assert "https://etender.uzex.uz/lot/525968" in sheet and "Veb-portal" in sheet


def test_xlsx_blocks_formulas():
    import io
    import zipfile

    from app.web import xlsx

    data = xlsx.build("T", ["a"], [["=HYPERLINK(\"x\")"], [5]], [10])
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert "'=HYPERLINK" in sheet and "<v>5</v>" in sheet


async def test_bulk_lot_actions(client, sessionmaker):
    await login(client)
    html = await (await client.get("/tender/lots")).text()
    assert 'name="ids"' in html and 'id="bulk"' in html and "Muddat" in html
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
    async with sessionmaker() as s:
        ids = [str(i) for i in (await s.scalars(select(Lot.id))).all()]
    r = await client.post("/tender/lots/bulk", data=[("csrf", token), ("action", "ignore"), ("back", "/tender/lots"),
                                                     *[("ids", i) for i in ids]], allow_redirects=False)
    assert r.status == 303 and r.headers["Location"] == "/tender/lots"
    async with sessionmaker() as s:
        assert {lot.status for lot in (await s.scalars(select(Lot))).all()} == {LotStatus.IGNORED}
    r = await client.post("/tender/lots/bulk", data={"csrf": "bad", "action": "bid"}, allow_redirects=False)
    assert r.status == 403
    assert (await client.get("/tender/static/app.js")).status == 200
