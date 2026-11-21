from app.db.models import Lot
from app.tenders import cards


def _lot(**kw) -> Lot:
    base = dict(source="etender", external_id="525968", title="Dasturiy ta'minot", url="https://etender.uzex.uz/lot/525968",
                start_cost=1e8, currency="UZS")
    return Lot(**{**base, **kw})


def test_one_line_has_clickable_lot_number():
    line = cards.one_line(_lot())
    assert '<a href="https://etender.uzex.uz/lot/525968">525968</a>' in line


def test_lot_link_escapes_and_rejects_non_http():
    assert cards.lot_link(_lot(url='https://x.uz/?a=1&b="2"')) == '<a href="https://x.uz/?a=1&amp;b=&quot;2&quot;">525968</a>'
    assert cards.lot_link(_lot(url="javascript:alert(1)")) == "525968"
    assert cards.lot_link(_lot(external_id="<1>", url="")) == "&lt;1&gt;"
