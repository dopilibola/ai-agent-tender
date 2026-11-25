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


class FakeAnalyzer:
    calls = 0
    max_per_day = 100

    async def requests_today(self):
        return 0

    async def __call__(self, lp, detail, *, big):
        self.calls += 1
        assert detail["description"] == "Maktabga kompyuter jihozlari" and len(lp.rows) == 3
        return {"summary_uz": "Printer va stullar.", "price_verdict": "QIMMAT", "price_comment_uz":
                "Printer 4 baravar qimmat.", "supplier_verdict": "SHARTLI", "supplier_reason_uz": "Foyda bor.",
                "est_margin_percent": [30, 60], "win_chance_percent": [20, 45], "key_conditions": [], "risks": ["Kafolat"], "red_flags": [],
                "item_notes": [], "questions_for_customer_uz": [],
                "item_prices": {"2": {"p": 13_000, "n": "A-92 benzin, 1 litr"}}}


@pytest.fixture
async def watch(settings, sessionmaker):
    settings.secrets.web_admin_password = SecretStr(PASSWORD)
    settings.config.web.base_path = "/tender"
    future = datetime.now(UTC) + timedelta(days=3)
    cards = {"10": LotRecord(source="etender", external_id="10", url="https://etender.uzex.uz/lot/10", title="t",
                             description="Maktabga kompyuter jihozlari", pledge_percent=3, pledge_amount=900_000,
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
    pw.analyzer = FakeAnalyzer()
    tenders.prices = pw
    return pw, src, uzum


async def test_run_and_compare(watch):
    pw, src, uzum = watch
    await pw.run()
    assert src.opened == ["10"]  # the service lot is not opened
    assert [t for _, t in uzum.queries] == ["Printer Canon", "Stul ofis uchun"]  # litres are not searched
    rows, state = await pw.comparison()
    benzin = {r.item.name: r for r in rows}["Benzin"]  # litres: not on Uzum, priced by the AI estimate
    assert benzin.status == "ai" and benzin.uzum_med == 13_000 and benzin.overpay == -100_000
    assert benzin.note == "A-92 benzin, 1 litr" and state["ai_priced"] == 1
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
        assert "Boshlang&#x27;ich summa" in html and "Zakalat" in html
        none = await (await c.get("/tender/all/uzum", params={"sm": "s5", "f": "all"})).text()
        assert "Printer Canon" not in none  # the 5 mlrd+ band has no lot here
        assert "Boshlang&#x27;ich narx" in html and "Shartli" in html and "20–45%" in html  # lot columns
        assert "&lt;b&gt;Maktab" in html and "<b>Maktab</b>" not in html
        assert "https://uzum.uz/uz/search?query=Printer%20Canon" in html
        every = await (await c.get("/tender/all/uzum", params={"f": "all"})).text()
        assert "Stul ofis" in every
        main = await (await c.get("/tender/all")).text()
        goods = await (await c.get("/tender/all")).text()
        assert "900 000 so&#x27;m" in goods or "900 ming so&#x27;m" in goods or "0.9 mln so&#x27;m" in goods
        assert "Shartli" in goods and "20–45%" in goods and "Jami: 1 ta lot" in goods
        assert 'href="/tender/all/uzum"' in main and "katta farqlar" in main and "×4.5 qimmat" in main
        top = main.split("katta farqlar", 1)[1].split("<h2", 1)[0]
        assert "Kiritiladigan pul (zakalat)" in top and "AI xulosa" in top and "Shartli" in top and "20–45%" in top
        lots = await (await c.get("/tender/all/uzum", params={"f": "lots"})).text()
        assert "3 / 3" in lots and "1 ta" in lots and 'data-href="/tender/all/uzum/lot/etender-10"' in lots
        one = await (await c.get("/tender/all/uzum/lot/etender-10")).text()
        assert "Lot ma'lumotlari" in one and "Maktabga kompyuter jihozlari" in one  # lot card details
        assert pw.analyzer.calls == 1 and "AI lotni tahlil qilmoqda" not in one  # done by the run itself
        assert "Narx: QIMMAT" in one and "Printer 4 baravar qimmat" in one and "Qayta tahlil" in one
        assert "<tfoot>" in one and "Jami: 3 ta mahsulot" in one and "+15.9 mln so&#x27;m" in one
        assert "🤖 13 000 so&#x27;m" in one and "A-92 benzin, 1 litr" in one
        assert 'data-href="#i-0"' in one and 'id="i-0"' in one  # row → the item's own details
        assert pw.analyzer.calls == 1
        r = await c.post("/tender/all/uzum/lot/etender-10/ai", data={"csrf": "bad"}, allow_redirects=False)
        assert r.status == 403
        assert "Printer Canon" in one and "Stul ofis" in one and "Benzin" in one  # every item of the lot
        assert "AI bozor narxi taxmini" in one and "Solishtirildi" in one
        assert "📄 Tender" in one and "🛒 Uzum" in one and 'href="https://etender.uzex.uz/lot/10"' in one
        assert "📄 Tender" in html and "🛒 Uzum" in html and "📄 Tender" in main
        assert (await c.get("/tender/all/uzum/lot/etender-999")).status == 404
        r = await c.get("/tender/all/uzum.xlsx", params={"f": "lots"})
        assert r.status == 200 and "uzum_lotlar" in r.headers["Content-Disposition"]
        r = await c.get("/tender/all/uzum.xlsx", params={"f": "all"})
        assert r.status == 200 and "uzum_narxlari" in r.headers["Content-Disposition"]


async def test_goods_analyzer(watch, sessionmaker):
    from app.tenders.goods_ai import GoodsAnalysis, GoodsAnalyzer
    from app.tenders.prices import group_by_lot

    pw, _, _ = watch
    await pw.run()
    rows, _ = await pw.comparison()
    lp = group_by_lot(rows)[0]
    seen = {}

    class FakeGemini:
        def model_for(self, role):
            return "gemini-test"

        async def generate_json(self, prompt, schema, **kw):
            seen["prompt"], seen["purpose"] = prompt, kw["purpose"]
            return GoodsAnalysis(summary_uz="s", price_verdict="QIMMAT", price_comment_uz="p",
                                 supplier_verdict="SHARTLI", supplier_reason_uz="r", est_margin_percent=[60, 20], win_chance_percent=[150],
                                 key_conditions=[" ", "to'lov 30 kun"], risks=[], red_flags=[],
                                 item_notes=[{"name": "Printer Canon", "note": "qimmat"}], questions_for_customer_uz=[],
                                 item_prices=[{"i": 2, "price_uzs": 13000, "basis": "benzin litr"},
                                              {"i": 0, "price_uzs": 1, "basis": "Uzum has it"}])

    data = await GoodsAnalyzer(FakeGemini(), sessionmaker)(lp, await pw.lot_detail(lp.lot), big=2.0)
    assert data["win_chance_percent"] == [100, 100]
    assert data["item_prices"] == {"2": {"p": 13000.0, "n": "benzin litr"}}  # only items Uzum could not price
    assert '"ai_narx_kerak": true' in seen["prompt"]
    assert data["est_margin_percent"] == [20, 60] and data["key_conditions"] == ["to'lov 30 kun"]
    assert seen["purpose"] == "goods_analysis" and "Printer Canon" in seen["prompt"] and "uzum_orta" in seen["prompt"]
    assert "Maktabga kompyuter jihozlari" in seen["prompt"] and data["_model"] == "gemini-test"
