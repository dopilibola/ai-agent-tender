"""FL.ru (RSS) and Kwork (embedded JSON) parsers on real pages saved 17.11.2026, plus FX cross rates."""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx
import pytest

from app.connectors.fl_ru import parse_item
from app.connectors.kwork import extract_wants, parse_want
from app.tenders.fx import UsdRate
from tests.conftest import FIXTURES


def test_fl_ru_feed():
    items = [parse_item(i) for i in ET.parse(FIXTURES / "fl_ru" / "feed.xml").getroot().findall(".//item")]
    assert len(items) == 4 and all(items)
    first = items[0]
    assert first.platform == "fl_ru" and first.external_id == "5529382"
    assert first.url.startswith("https://www.fl.ru/projects/5529382/")
    assert "(для всех)" not in first.title and first.language == "ru"
    assert first.posted_at == datetime(2026, 11, 16, 8, 52, 23, tzinfo=UTC)
    assert first.skills[0] == "Программирование"
    with_budget = [i for i in items if i.budget_max]
    assert with_budget and with_budget[0].currency == "RUB"
    assert "Бюджет" not in with_budget[0].title


def test_kwork_page():
    wants = extract_wants((FIXTURES / "kwork" / "projects.html").read_text(encoding="utf-8"))
    assert len(wants) == 3
    job = parse_want(wants[0])
    assert job.platform == "kwork" and job.url == f"https://kwork.ru/projects/{job.external_id}"
    assert job.currency == "RUB" and job.budget_max == 7000 and job.raw["max_offer_price"] == 21000
    assert job.proposals_count == 76
    assert job.posted_at.utcoffset().total_seconds() == 3 * 3600  # Moscow time
    assert job.raw["client"]["hired_percent"] == 0.0


def test_kwork_layout_change_is_loud():
    with pytest.raises(ValueError):
        extract_wants("<html>no data</html>")


async def test_usd_cross_rate(sessionmaker):
    rows = [{"Ccy": "USD", "Rate": "12000", "Nominal": "1", "Date": "17.11.2026"},
            {"Ccy": "RUB", "Rate": "150", "Nominal": "1"},
            {"Ccy": "INR", "Rate": "1350", "Nominal": "10"}]
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=rows))
    fx = UsdRate(sessionmaker, transport=transport)
    assert await fx.usd_per_unit("USD") == 1.0
    assert await fx.usd_per_unit("RUB") == pytest.approx(0.0125)
    assert await fx.usd_per_unit("INR") == pytest.approx(0.01125)
    assert await fx.usd_per_unit("UZS") == pytest.approx(1 / 12000)
    assert await fx.usd_per_unit("XXX") is None
