from datetime import UTC, datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from app.tenders.prices import (
    Hit,
    PriceWatch,
    is_piece,
    items_from_record,
    match_stats,
    relevant,
    search_query,
)
from app.tenders.schemas import LotRecord, LotSummary
from app.tenders.service import TenderService
from app.web.server import build_app
from tests.test_web import PASSWORD, Sender, login


def test_search_query_locale():
    assert search_query("Қоғоз A4 (офис учун)") == ("uz", "Qog'oz A4")
    assert search_query("Бензин автомобильный") == ("ru", "Бензин автомобильный")
    assert search_query("Printer Canon; kartrij") == ("uz", "Printer Canon")
    assert search_query("Комбинацияланган жавон") == ("uz", "Kombinatsiyalangan javon")


def test_units():
    assert is_piece("дона") and is_piece("шт.") and is_piece("") and is_piece("Комплект")
    assert not is_piece("литр") and not is_piece("кг") and not is_piece("метр")


def test_relevance_and_stats():
    assert relevant("Printer Canon", "Rangli printer Canon PIXMA G3430")
    assert not relevant("Printer Canon", "Telefon g'ilofi")
    assert relevant("Ko'pirtirilgan polipropilendan ishlangan bolalar karavoti", "Bolalar karavoti, sinchsiz")
    assert not relevant("Kasalxonalar uchun jihoz", "Kapa saqlash uchun stomatologik konteyner")
    assert not relevant("Antilymphocyte immunoglobulin", "NOVIREL tomchilari — immunitetni qo'llab-quvvatlash")
    assert relevant("Бумага офисная А4", "Бумага офисная SvetoCopy A4, 500 листов", "ru")
    assert not relevant("Бумага офисная", "Офисный стул", "ru")
    hits = [Hit("Printer Canon G3430", 2_000_000, "u1"), Hit("Printer Canon E414", 900_000, "u2"),
            Hit("Printer Canon G2430", 1_800_000, "u3"), Hit("Kartrij uchun siyoh", 50_000, "u4")]
    med, mn, n, best = match_stats("Printer Canon", hits)
    assert (med, mn, n) == (1_800_000, 900_000, 3) and best.url == "u3"
    assert match_stats("Printer Canon", hits[:2])[0] is None  # fewer than 3 matches → no comparison


def test_merged_skips_families_and_joins_duplicates():
    from app.tenders.prices import Item, _merged
    rows = _merged([Item("Shina", 4, "дона", 500_000), Item("Shina", 4, "дона", 7_000_000),
                    Item("Javon", 2, "дона", 1_000_000), Item("Javon", 3, "дона", 1_000_000)])
    assert [(i, it.name, it.qty, fam) for i, it, fam in rows] == [
        (0, "Shina", 4, True), (1, "Shina", 4, True), (2, "Javon", 5, False)]


def test_items_from_records():
    et = LotRecord(source="etender", external_id="1", url="u", title="t", raw={"budget_products": [
        {"Product_Name": "Printer", "Quantity": 5, "Price": 3_000_000, "Product_Code": "26.20",
         "Js_Properties": [{"Property_Name": "Ўлчов бирлиги", "User_Value": "дона"}]}]})
    xa = LotRecord(source="xarid", external_id="2", url="u", title="t", raw={"details": [
        {"productName": "Бензин", "quantity": 900, "price": 12000,
         "properties": [{"propertyName": "Единица измерения", "propertyValueName": "литр"}]}]})
    (a,), (b,) = items_from_record(et), items_from_record(xa)
    assert (a.name, a.qty, a.unit, a.price) == ("Printer", 5, "дона", 3_000_000)
    assert (b.name, b.unit, b.price) == ("Бензин", "литр", 12000)


class FakeSource:
    def __init__(self, name, cards):
        self.name = self.title = name
        self.cards = cards
        self.opened = []

    async def get_lot(self, external_id):
        self.opened.append(external_id)
        return self.cards[external_id]


class FakePhone:
    async def ensure(self):
        pass


class FakeUzum:
    phone = FakePhone()

    def __init__(self):
        self.queries = []

    async def search(self, locale, text):
        self.queries.append((locale, text))
        if text.startswith("Printer"):
            return [Hit(f"Printer Canon {i}", p, f"https://uzum.uz/p{i}") for i, p in
                    enumerate([1_000_000, 1_200_000, 1_100_000])]
        if text.startswith("Stul"):
            return [Hit(f"Ofis stul {i}", 500_000, f"https://uzum.uz/s{i}") for i in range(4)]
        return []


@pytest.fixture
async def watch(settings, sessionmaker):
    settings.secrets.web_admin_password = SecretStr(PASSWORD)
    settings.config.web.base_path = "/tender"
    future = datetime.now(UTC) + timedelta(days=3)
    cards = {"10": LotRecord(source="etender", external_id="10", url="https://etender.uzex.uz/lot/10", title="t",
                             raw={"budget_products": [
                                 {"Product_Name": "Printer Canon", "Quantity": 4, "Price": 5_000_000,
                                  "Js_Properties": [{"Property_Name": "Ўлчов бирлиги", "User_Value": "дона"}]},
                                 {"Product_Name": "Stul ofis uchun", "Quantity": 20, "Price": 520_000},
                                 {"Product_Name": "Benzin", "Quantity": 100, "Price": 12_000,
                                  "Js_Properties": [{"Property_Name": "Ўлчов бирлиги", "User_Value": "литр"}]},
                             ]})}
    src = FakeSource("etender", cards)
    tenders = TenderService(settings, sessionmaker, {"etender": src}, Sender())
    now = datetime.now(UTC)
    await tenders._save_catalog("etender", [
        LotSummary(source="etender", external_id="10", url="https://etender.uzex.uz/lot/10",
                   title="Printer va stullarni yetkazib berish", deadline_at=future, start_cost=3e7,
                   customer_name="<b>Maktab</b>"),
        LotSummary(source="etender", external_id="11", url="u", title="Bino ta'mirlash ishlari", deadline_at=future),
    ], now)
    uzum = FakeUzum()
    pw = PriceWatch(settings, sessionmaker, tenders, uzum=uzum)
    tenders.prices = pw
    return pw, src, uzum


async def test_run_and_compare(watch):
    pw, src, uzum = watch
    await pw.run()
    assert src.opened == ["10"]  # the service lot is not opened
    assert [t for _, t in uzum.queries] == ["Printer Canon", "Stul ofis uchun"]  # litres are not searched
    rows, state = await pw.comparison()
    assert {r.item.name: r.status for r in rows}["Benzin"] == "unit"
    rows = [r for r in rows if r.status == "ok"]
    by = {r.item.name: r for r in rows}
    assert by["Printer Canon"].uzum_med == 1_100_000 and round(by["Printer Canon"].ratio, 1) == 4.5
    assert by["Printer Canon"].overpay == (5_000_000 - 1_100_000) * 4
    assert round(by["Stul ofis uchun"].ratio, 2) == 1.04
    assert state["compared"] == 2 and state["pending"] == 0 and state["phone"] == "ok"
    await pw.run()  # cached: no second lot card, no second search
    assert src.opened == ["10"] and len(uzum.queries) == 2


async def test_prices_page(watch, settings, sessionmaker):
    pw, _, _ = watch
    await pw.run()
    app = build_app(settings, sessionmaker, tenders=pw.tenders, freelance=None)
    async with TestClient(TestServer(app)) as c:
        assert (await c.get("/tender/all/uzum", allow_redirects=False)).status == 303
        await login(c)
        html = await (await c.get("/tender/all/uzum")).text()
        assert "Uzum narxlari" in html and "Printer Canon" in html and "×4.5 qimmat" in html
        assert "Stul ofis" not in html  # 1.04× is not a big difference
        assert "&lt;b&gt;Maktab" in html and "<b>Maktab</b>" not in html
        assert "https://uzum.uz/uz/search?query=Printer%20Canon" in html
        every = await (await c.get("/tender/all/uzum", params={"f": "all"})).text()
        assert "Stul ofis" in every
        main = await (await c.get("/tender/all", params={"k": "it"})).text()
        assert 'href="/tender/all/uzum"' in main and "katta farqlar" in main and "×4.5 qimmat" in main
        lots = await (await c.get("/tender/all/uzum", params={"f": "lots"})).text()
        assert "2 / 3" in lots and "1 ta" in lots and 'data-href="/tender/all/uzum/lot/etender-10"' in lots
        one = await (await c.get("/tender/all/uzum/lot/etender-10")).text()
        assert "Printer Canon" in one and "Stul ofis" in one and "Benzin" in one  # every item of the lot
        assert "O&#x27;lchov birligi" in one and "Solishtirildi" in one
        assert (await c.get("/tender/all/uzum/lot/etender-999")).status == 404
        r = await c.get("/tender/all/uzum.xlsx", params={"f": "lots"})
        assert r.status == 200 and "uzum_lotlar" in r.headers["Content-Disposition"]
        r = await c.get("/tender/all/uzum.xlsx", params={"f": "all"})
        assert r.status == 200 and "uzum_narxlari" in r.headers["Content-Disposition"]
