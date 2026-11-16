"""xarid.uzex.uz «Taklif so'rovi» parsing on real API responses saved 17.11.2026."""

import json

from app.tenders.filters import KeywordFilter
from app.tenders.sources.xarid import _data, parse_list_row, parse_lot
from app.tenders.timeutil import fmt_local
from tests.conftest import FIXTURES


def load(name):
    return json.loads((FIXTURES / "xarid" / name).read_text(encoding="utf-8"))


def test_list_rows_are_categories():
    rows = [parse_list_row(r) for r in _data(load("list.json"))]
    trimmer = next(r for r in rows if r.external_id == "139678")
    assert trimmer.title_is_category and trimmer.title == "Изделия резиновые и пластмассовые"
    assert trimmer.url == "https://xarid.uzex.uz/request-proposal/3/detail/139678"
    assert trimmer.start_cost == 19_994_000 and trimmer.currency == "UZS"
    assert fmt_local(trimmer.deadline_at) == "17.11.2026 07:36"


def test_category_prefilter(settings):
    kf = KeywordFilter(settings.config.tenders)
    rows = {r.external_id: r for r in (parse_list_row(x) for x in _data(load("list.json")))}
    assert kf.match(rows["139141"].title).is_it  # «Продукты программные … программного обеспечения»
    assert not kf.match(rows["139678"].title).is_it  # rubber & plastic products
    hw = next(r for r in rows.values() if "компьютерное" in r.title)
    m = kf.match(hw.title)
    assert not m.is_it and m.excluded_by  # computer hardware is never opened


def test_it_lot_card():
    rec = parse_lot(_data(load("lot_it.json")))
    assert rec.external_id == "139141" and rec.is_open and rec.platform_status == "Опубликован"
    assert "программного обеспечения" in rec.title
    assert rec.start_cost == 40_000_000 and rec.price_weight == 100
    assert rec.files and rec.files[0].kind == "tz" and rec.files[0].ext == "pdf"
    assert rec.files[0].path.isdigit()  # downloaded via /api/Files/GetLotFileById?id=
    assert "Pozitsiyalar:" in rec.description


def test_goods_lot_card():
    rec = parse_lot(_data(load("lot_trimmer.json")))
    assert rec.title == "Триммер бензиновый" or rec.title.startswith("Триммер бензиновый")
    assert rec.advance_percent == 30.0  # 5 998 200 / 19 994 000
    assert rec.files[0].ext == "xlsx"
