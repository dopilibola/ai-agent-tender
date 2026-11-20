from datetime import timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.db.models import Lot, LotStatus
from app.tenders.timeutil import now_utc
from app.web.server import build_app


async def _seed(sessionmaker):
    async with sessionmaker() as s:
        s.add_all([
            Lot(source="etender", external_id="524524", url="https://etender.uzex.uz/lot/524524",
                title="Axborot tizimi <ishlab chiqish>", start_cost=150_000_000, currency="UZS", usd_rate=12_000,
                deadline_at=now_utc() + timedelta(days=3), status=LotStatus.NOTIFIED, verdict="QATNASHAMIZ",
                analysis={"fit_score": 82}),
            Lot(source="xarid", external_id="777", url="https://xarid.uzex.uz/777", title="Kerak emas lot",
                status=LotStatus.IGNORED),
        ])
        await s.commit()


@pytest.fixture
async def client_factory(sessionmaker):
    clients = []

    async def make(token=None):
        c = TestClient(TestServer(build_app(sessionmaker, token)))
        await c.start_server()
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.close()


async def test_lots_page_lists_active_lots_escaped(sessionmaker, client_factory):
    await _seed(sessionmaker)
    client = await client_factory()
    r = await client.get("/tender/lots")
    assert r.status == 200
    html = await r.text()
    assert "524524" in html and "&lt;ishlab chiqish&gt;" in html
    assert "Kerak emas lot" not in html
    assert "Kerak emas lot" in await (await client.get("/tender/lots?all=1")).text()


async def test_root_redirects_and_json(sessionmaker, client_factory):
    await _seed(sessionmaker)
    client = await client_factory()
    r = await client.get("/", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/tender/lots"
    data = await (await client.get("/tender/lots.json")).json()
    assert data["count"] == 1 and data["lots"][0]["fit_score"] == 82


async def test_token_required_and_cookie(sessionmaker, client_factory):
    client = await client_factory(token="s3cret")
    assert (await client.get("/health")).status == 200
    assert (await client.get("/tender/lots")).status == 401
    assert (await client.get("/tender/lots?key=wrong")).status == 401
    assert (await client.get("/tender/lots?key=s3cret")).status == 200
    assert (await client.get("/tender/lots")).status == 200  # cookie set by the previous request
