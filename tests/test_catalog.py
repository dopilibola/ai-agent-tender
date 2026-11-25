from datetime import UTC, datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr

from app.tenders.catalog import classify
from app.tenders.filters import KeywordFilter
from app.tenders.schemas import LotSummary
from app.tenders.service import TenderService
from app.web.server import build_app
from tests.test_web import PASSWORD, Sender, login


def et(title: str, i: str = "1", **kw) -> LotSummary:
    return LotSummary(source="etender", external_id=i, url=f"https://etender.uzex.uz/lot/{i}", title=title, **kw)


def xa(category: str, i: str = "1", **kw) -> LotSummary:
    return LotSummary(source="xarid", external_id=i, url=f"https://xarid.uzex.uz/request-proposal/3/detail/{i}",
                      title=category, title_is_category=True, **kw)


@pytest.mark.parametrize("title,kind", [
    ("O‘zRVB ehtiyojlari uchun turli xil elektr yoritish uskunalari va yoritish moslamalarini yetkazib berish.", "goods"),
    ("Тил ва адабиёт таълими журналини Кашкадарё вилоятидаги мактабларга етказиб бериш", "goods"),
    ("Nasos agregatini xarid qilish uchun (на закупку насосного агрегата)", "goods"),
    ("“Aeronavigatsiya axborotini boshqarish raqamli tizimi” uchun butlovchi qismlarni xarid qilish", "goods"),
    ("Ofis qog'ozi A4", "goods"),
    ("To‘siq o‘rnatish xizmati", "service"),
    ("Bino va inshootlarni baholash xizmati", "service"),
    ("Янгиқўрғон туман шифохонаси биносини реконструкция қилиш. Топосёмка ишларини бажариш", "service"),
    ("Услуги по выполнению СМР по объекту Реконструкция насосной", "service"),
    ("Qurilish-Texnik nazorat boʻyicha Konsalting tashkilotini jalb qilish", "service"),
])
def test_classify_etender(settings, title, kind):
    assert classify(et(title), KeywordFilter(settings.config.tenders)) == kind


@pytest.mark.parametrize("category,kind", [
    ("Уголь", "goods"), ("Кокс и нефтепродукты", "goods"), ("Одежда", "goods"),
    ("Продукция и услуги сельского хозяйства и охоты", "goods"),
    ("Услуги почтовой связи и услуги курьерские", "service"),
    ("Здания и работы по возведению зданий", "service"), ("Работы строительные специализированные", "service"),
])
def test_classify_xarid_category(settings, category, kind):
    assert classify(xa(category), KeywordFilter(settings.config.tenders)) == kind


def test_it_lot_is_labelled_it(settings):
    kf = KeywordFilter(settings.config.tenders)
    assert classify(et("Veb-saytni ishlab chiqish va dasturiy ta'minot yaratish"), kf) == "it"


class FakeSource:
    def __init__(self, name):
        self.name = self.title = name


@pytest.fixture
async def client(settings, sessionmaker):
    settings.secrets.web_admin_password = SecretStr(PASSWORD)
    settings.config.web.base_path = "/tender"
    tenders = TenderService(settings, sessionmaker, {"etender": FakeSource("etender"), "xarid": FakeSource("xarid")},
                            Sender())
    now = datetime.now(UTC)
    future, past = now + timedelta(days=3), now - timedelta(hours=1)
    await tenders._save_catalog("etender", [
        et("Kompyuter stollarini yetkazib berish", "10", deadline_at=future, start_cost=5e7,
           customer_name="<b>Maktab</b>", customer_region="Toshkent"),
        et("Bino ta'mirlash ishlari", "11", deadline_at=future),
        et("Eski stul yetkazib berish", "12", deadline_at=past),
    ], now)
    await tenders._save_catalog("xarid", [xa("Уголь", "20", deadline_at=future, customer_region="Andijon")], now)
    app = build_app(settings, sessionmaker, tenders=tenders, freelance=None)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_catalog_page(client):
    assert (await client.get("/tender/all", allow_redirects=False)).status == 303  # login first
    await login(client)
    html = await (await client.get("/tender/all")).text()
    assert "Tovar va xizmat tenderlari" in html and 'href="/tender/all"' in html
    assert ">IT tenderlar<" in html and ">Frilans ishlar<" in html  # sections stay separate in the menu
    r = await client.get("/tender/all", params={"k": "it"}, allow_redirects=False)
    assert r.status == 303 and r.headers["Location"] == "/tender/lots"  # IT lots are not mixed in here
    assert "Kompyuter stollarini" in html and "Уголь" in html
    assert "ta'mirlash ishlari" not in html  # service, not on the goods tab
    assert "Eski stul" not in html  # deadline passed
    assert "<b>Maktab</b>" not in html and "&lt;b&gt;Maktab" in html
    assert 'data-href="/tender/all/uzum/lot/etender-10"' in html  # goods lot → our page with the AI summary
    assert "Kiritiladigan pul (zakalat)" in html and "Yutish %" in html and "Hammasida qatnashish uchun" in html
    assert 'data-ext="1"' in await (await client.get("/tender/all", params={"k": "service"})).text()
    svc = await (await client.get("/tender/all", params={"k": "service"})).text()
    assert "ta&#x27;mirlash ishlari" in svc and "Kompyuter stollarini" not in svc
    only_xarid = await (await client.get("/tender/all", params={"src": "xarid"})).text()
    assert "Уголь" in only_xarid and "Kompyuter stollarini" not in only_xarid
    reg = await (await client.get("/tender/all", params={"r": "Toshkent"})).text()
    assert "Kompyuter stollarini" in reg and "Уголь" not in reg
    furn = await (await client.get("/tender/all", params={"y": "furniture"})).text()
    assert "Kompyuter stollarini" in furn and "Уголь" not in furn
    assert "Mebel va inventar <span>1</span>" in html and "Yoqilg&#x27;i va energiya <span>1</span>" in html
    assert "Boshlang&#x27;ich summa" in html and "Zakalatsiz" in html and "5 mlrd dan ko&#x27;p" in html
    small = await (await client.get("/tender/all", params={"sm": "s1"})).text()  # 50 mln so'm lot
    assert "Kompyuter stollarini" in small and "Уголь" not in small  # Уголь has no price
    big = await (await client.get("/tender/all", params={"sm": "s4,s5"})).text()
    assert "Kompyuter stollarini" not in big and "2 ta tanlangan" not in big
    both = await (await client.get("/tender/all", params={"y": "furniture,fuel"})).text()  # several at once
    assert "Kompyuter stollarini" in both and "Уголь" in both and "2 ta tanlangan" in both
    assert 'href="/tender/all?y=fuel' in both  # clicking a chosen one removes it
    fuel = await (await client.get("/tender/all", params={"y": "fuel"})).text()
    assert "Уголь" in fuel and "Kompyuter stollarini" not in fuel
    assert (await client.get("/tender/all", params={"y": "nonsense"})).status == 200
    r = await client.get("/tender/all.xlsx", params={"k": "service"})
    assert r.status == 200 and r.headers["Content-Disposition"].startswith('attachment; filename="xizmat_tenderlar')


@pytest.mark.parametrize("title,key", [
    ("Kompyuter stollarini yetkazib berish", "furniture"), ("Кокс и нефтепродукты", "fuel"),
    ("Колбаса махсулотларини харид қилиш", "food"), ("PQ-20-sonli qaror doirasida tibbiy buyumlarni xarid qilish", "medical"),
    ("Оборудование компьютерное, электронное и оптическое", "tech"), ("Diodli lampa", "electric"),
    ("Огнеупорные материалы и припасы для футеровки печей / ўтга чидамли материаллар ва жиҳозлар", "build"),
    ("Услуги почтовой связи и услуги курьерские", "transport"), ("Отбор наилучшего предложения", "other"),
])
def test_direction(title, key):
    from app.tenders.catalog import direction
    assert direction(title) == key
