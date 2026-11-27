"""🤖 AI chat about one lot: link parsing, opening a lot from any source, questions, the web pages."""

import asyncio
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from app.tenders.chat import ChatError, LotChat, parse_ref
from app.tenders.schemas import LotFile, LotRecord
from app.tenders.service import TenderService
from app.web import views
from app.web.server import build_app

PASSWORD = "s3cret-pass"


@pytest.mark.parametrize("ref, want", [
    ("https://etender.uzex.uz/lot/527202", ("etender", "527202")),
    ("etender.uzex.uz/lot/527202?x=1", ("etender", "527202")),
    ("https://xarid.uzex.uz/request-proposal/3/detail/148038", ("xarid", "148038")),
    ("https://xarid.uzex.uz/purchase/competition/detail/25566", ("xarid_tanlov", "25566")),
    ("https://xarid.uzex.uz/auction/detail/425020", ("xarid_auction", "425020")),
    ("https://xt-xarid.uz/procedure/9845418/core", ("hayotbirja", "9845418")),
    ("https://hayotbirja.uz/procedure/9845418", ("hayotbirja", "9845418")),
    ("hayotbirja-9845418", ("hayotbirja", "9845418")),
])
def test_parse_ref(ref, want):
    assert parse_ref(ref) == want


@pytest.mark.parametrize("ref", ["https://evil.example/lot/1", "https://etender.uzex.uz.evil.com/lot/1",
                                 "https://etender.uzex.uz/about", "", "javascript:alert(1)"])
def test_unknown_links_are_refused(ref):
    with pytest.raises(ChatError):
        parse_ref(ref)


class FakeSource:
    name = "etender"
    title = "etender.uzex.uz"

    def __init__(self):
        self.calls = []

    async def get_lot(self, external_id):
        self.calls.append(external_id)
        return LotRecord(source="etender", external_id=external_id, url=f"https://etender.uzex.uz/lot/{external_id}",
                         title="Axborot tizimi modullarini ishlab chiqish", description="Batafsil tavsif",
                         start_cost=10_650_000_000, customer_name="Yagona integrator", platform_status="Faol",
                         files=[LotFile(kind="tz", label="Texnik topshiriq", name="tz.txt", path="f1", ext="txt")])

    async def download(self, file, dest: Path, *, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("TEXNIK TOPSHIRIQ: ijrochida kamida 3 ta Python dasturchi bo'lishi shart. " * 20)
        return dest.stat().st_size


class FakeGemini:
    def __init__(self):
        self.prompts = []

    def model_for(self, role):
        return "fake"

    async def generate_text(self, prompt, **kw):
        self.prompts.append((prompt, kw))
        return "**3 ta** Python dasturchi kerak (TZ).\n- birinchi\n- ikkinchi"


@pytest.fixture
def world(settings, sessionmaker):
    src = FakeSource()
    tenders = TenderService(settings, sessionmaker, {"etender": src}, None)
    gem = FakeGemini()
    chat = LotChat(gem, settings, sessionmaker, tenders)
    tenders.chat = chat
    return chat, src, gem, tenders


async def _ready(chat, key):
    for _ in range(100):
        data = await chat.get(key)
        if data["status"] != "loading":
            return data
        await asyncio.sleep(0.02)
    raise AssertionError("still loading")


async def test_open_reads_card_and_documents_then_answers(world):
    chat, src, gem, _ = world
    key = await chat.open("https://etender.uzex.uz/lot/527202")
    assert key == "etender-527202"
    data = await _ready(chat, key)
    assert data["status"] == "ready" and data["title"].startswith("Axborot tizimi")
    assert "3 ta Python dasturchi" in data["context"] and data["docs"][0]["label"] == "Texnik topshiriq"

    answer = await chat.ask(key, "Nechta dasturchi kerak?")
    assert "3 ta" in answer["text"]
    prompt, kw = gem.prompts[-1]
    assert kw["purpose"] == "lot_chat" and "Nechta dasturchi kerak?" in prompt
    assert "<untrusted-" in prompt and "KOMPANIYA PROFILI" in prompt  # documents are data, profile is trusted
    data = await chat.get(key)
    assert [m["role"] for m in data["messages"]] == ["user", "ai"]

    # second question carries the first turn as history; opening again does not re-read the lot
    await chat.ask(key, "Yana?")
    assert "Nechta dasturchi kerak?" in gem.prompts[-1][0].split("## YANGI SAVOL")[0]
    assert await chat.open(key) == key and src.calls == ["527202"]


async def test_disabled_source_and_daily_limit(world, settings):
    chat, _, _, _ = world
    with pytest.raises(ChatError, match="o'chirilgan"):
        await chat.open("https://xt-xarid.uz/procedure/1/core")
    key = await chat.open("etender-1")
    await _ready(chat, key)
    chat.cfg.max_requests_per_day = 0
    with pytest.raises(ChatError, match="limiti"):
        await chat.ask(key, "Savol")


def test_markdown_is_escaped():
    out = views._md("**Qalin** <script>x</script>\n- bir\n- ikki\n\n1. uch")
    assert "<b>Qalin</b>" in out and "&lt;script&gt;" in out and "<script>" not in out
    assert "<ul><li>bir</li><li>ikki</li></ul>" in out and "<ol><li>uch</li></ol>" in out
    quoted = views._md("Javob\n\n---\n\n> **Xulosa:**\n> qatnasha olmaysiz")
    assert "<hr>" in quoted and "<blockquote><p><b>Xulosa:</b><br>qatnasha olmaysiz</p></blockquote>" in quoted


@pytest.fixture
async def client(settings, sessionmaker, world):
    chat, _, _, tenders = world
    settings.secrets.web_admin_password = SecretStr(PASSWORD)
    settings.config.web.base_path = "/tender"
    app = build_app(settings, sessionmaker, tenders=tenders, freelance=None)
    async with TestClient(TestServer(app)) as c:
        r = await c.post("/tender/login", data={"user": "admin", "password": PASSWORD, "next": "/tender/"},
                         allow_redirects=False)
        assert r.status == 303
        yield c


async def _csrf(client, path):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', await (await client.get(path)).text()).group(1)


async def test_chat_pages(client, world):
    chat = world[0]
    first = await client.get("/tender/chat")
    html = await first.text()
    assert "Lotni ochish" in html and "AI chat" in html
    assert "connect-src 'self'" in first.headers["Content-Security-Policy"]  # the page's fetch() may reach us
    token = await _csrf(client, "/tender/chat")

    bad = await client.post("/tender/chat", data={"csrf": token, "url": "https://evil.example/lot/1"},
                            allow_redirects=False)
    assert bad.status == 400 and "Havola tanilmadi" in await bad.text()

    r = await client.post("/tender/chat", data={"csrf": token, "url": "https://etender.uzex.uz/lot/527202"},
                          allow_redirects=False)
    assert r.status == 303 and r.headers["Location"] == "/tender/chat/etender-527202"
    await _ready(chat, "etender-527202")
    page = await (await client.get("/tender/chat/etender-527202")).text()
    assert "Axborot tizimi modullarini" in page and 'name="q"' in page and "Texnik topshiriq" in page

    j = await client.post("/tender/chat/etender-527202/ask", data={"csrf": token, "q": "Nechta?"},
                          headers={"X-Requested-With": "fetch"})
    body = await j.json()
    assert body["ok"] and "<b>3 ta</b>" in body["html"]
    no_csrf = await client.post("/tender/chat/etender-527202/ask", data={"q": "x"})
    assert no_csrf.status == 403
    assert (await client.get("/tender/chat/../etc")).status == 404
