"""xarid tanlov (API), xarid auksion and Hayot birja (phone pages) parsing on real data saved 27.11.2026."""

import json
from datetime import UTC, datetime

import pytest

from app.tenders.prices import items_from_record
from app.tenders.sources import build_sources
from app.tenders.sources import hayotbirja as hb
from app.tenders.sources import xarid_auction as xa
from app.tenders.sources import xarid_tanlov as xt
from app.tenders.timeutil import fmt_local
from tests.conftest import FIXTURES

NOW = datetime(2026, 11, 27, 8, 20, tzinfo=UTC)  # 13:20 Tashkent, when the pages were saved


def load(path):
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


# ── xarid tanlov ──────────────────────────────────────────────────────────
def test_tanlov_list_rows():
    rows = [xt.parse_list_row(r) for r in load("xarid_tanlov/list.json")]
    assert len(rows) == 200 and all(r.title_is_category and r.lot_type == "Tanlov" for r in rows)
    first = rows[0]
    assert first.url == f"https://xarid.uzex.uz/purchase/competition/detail/{first.external_id}"
    assert first.customer_region and first.deadline_at and first.start_cost


def test_tanlov_it_card():
    rec = xt.parse_lot(load("xarid_tanlov/lot.json"))
    assert "программного обеспечения" in rec.title and rec.platform_status == "Опубликован"
    assert rec.customer_name and rec.customer_tin
    assert "Pozitsiyalar:" in rec.description and "Fayllar (lot sahifasida)" in rec.description
    items = items_from_record(rec)
    assert items and items[0].price > 0 and items[0].qty == 1


# ── xarid auksion ─────────────────────────────────────────────────────────
def test_auction_list_page():
    page = load("xarid_auction/list_p1.json")
    assert page["total"] == 544 and page["page"] == 1 and len(page["rows"]) == 20
    r = xa.parse_list_row(page["rows"][0])
    assert r.external_id == "425055" and r.title == "Изделия готовые прочие" and r.title_is_category
    assert r.start_cost == 3_000_000 and fmt_local(r.deadline_at) == "30.11.2026 10:00"
    assert r.customer_region == "Хорезмская область, Шаватский район"


def test_auction_card_items():
    rec = xa.parse_lot("425020", load("xarid_auction/lot_425020.json"))
    assert rec.title == "Анализатор влажности" and rec.customer_name == '"NAVOIYAZOT" AJ'
    assert rec.platform_status == "Опубликован" and rec.execution_days == 50
    assert [(i["q"], i["p"]) for i in rec.raw["items"]] == [(2.0, 543_705_804.0), (1.0, 467_965_692.0)]
    assert rec.start_cost == 2 * 543_705_804 + 467_965_692
    one = xa.parse_lot("425055", load("xarid_auction/lot_425055.json"))
    assert one.title == "Комплект ударных инструментов" and one.raw["items"][0]["u"] == "компл"


def test_money_and_dates():
    assert xa.parse_money("Boshlang‘ich narx: 1 555 377 300,00 So'm") == 1_555_377_300
    assert fmt_local(xa.parse_dt("Tugash muddati: 30.11.2026, 10:27:00")) == "30.11.2026 10:27"


# ── Hayot birja ───────────────────────────────────────────────────────────
def test_hayotbirja_list_cards():
    cards = load("hayotbirja/list_selection.json")
    parsed = [hb.parse_card("selection", r, NOW) for r in cards]
    open_ = [p for p, status in parsed if hb.is_open_status(status)]
    assert len(open_) == 12
    lot = next(p for p in open_ if p.external_id == "9898143")
    assert lot.start_cost == 180_000_000 and lot.currency == "UZS" and lot.lot_type == "Eng yaxshi taklifni tanlash"
    assert lot.customer_name.startswith('"Ромитан') and lot.url == "https://xt-xarid.uz/procedure/9898143/core"
    assert lot.deadline_at and lot.deadline_at > NOW  # now + «6 кун 20:52:08» left


def test_hayotbirja_closed_are_dropped():
    parsed = [hb.parse_card("tender", r, NOW) for r in load("hayotbirja/list_tender.json")]
    assert sum(hb.is_open_status(s) for _, s in parsed) == 7  # 27 «ёпилган», 9 «ўтказилмади» …


def test_hayotbirja_generic_title_replaced_by_items():
    rec = hb.parse_lot("9761468", load("hayotbirja/detail_tender.json"))
    assert rec.title == "Сунъий қон айланиши аппаратлари жамланмаси" and rec.is_open
    assert rec.raw["items"] == [{"n": "Сунъий қон айланиши аппаратлари жамланмаси", "q": 1.0, "u": "жамланма",
                                 "p": 4_320_000_000.0}]
    rp = hb.parse_lot("9898605", load("hayotbirja/detail_request_proposals.json"))
    assert rp.title == "Махсус техникани таъмирлаш хизмати" and rp.customer_name == "Buxoro Xududiy Elektr Tarmoqlari"
    auc = hb.parse_lot("9720777", load("hayotbirja/detail_reduction.json"))
    assert len(auc.raw["items"]) == 14 and auc.raw["items"][0]["n"] == "Металл оқава қувур"
    assert "?" not in auc.description  # no price/qty → no «? × ?»


def test_time_left():
    assert hb.time_left(["Осталось", "6 кун 20:52:08"]).total_seconds() == 6 * 86400 + 20 * 3600 + 52 * 60 + 8
    assert hb.time_left(["00:07:40"]).total_seconds() == 460
    assert hb.time_left(["Максимал қиймати:"]) is None


def test_build_sources_without_phone(settings):
    names = set(build_sources(settings.config.tenders))
    assert {"etender", "xarid", "xarid_tanlov"} <= names and not names & {"xarid_auction", "hayotbirja"}


def test_build_sources_with_phone(settings, tmp_path):
    names = set(build_sources(settings.config.tenders, phone_cfg=settings.config.phone, data_dir=tmp_path))
    assert {"xarid_auction", "hayotbirja"} <= names


class _Sections(hb.HayotbirjaSource):
    """Hayot birja with the phone replaced by saved list pages; `bad` sections fail `fails[s]` times."""

    def __init__(self, tmp_path, fails):
        super().__init__(browser=None, cache_dir=tmp_path, sections=["tender", "selection"])
        self.fails = dict(fails)
        self.calls = []

    async def _section(self, section):
        self.calls.append(section)
        if self.fails.get(section, 0) > 0:
            self.fails[section] -= 1
            raise ValueError("ro'yxat bo'sh (sahifa vaqtida yuklanmadi)")
        return load(f"hayotbirja/list_{section}.json")


async def test_hayotbirja_section_retried_once(tmp_path):
    src = _Sections(tmp_path, {"selection": 1})
    lots = await src.list_open()
    assert src.calls == ["tender", "selection", "selection"]
    assert len(lots) == 7 + 12


async def test_hayotbirja_partial_list_is_not_published(tmp_path):
    src = _Sections(tmp_path, {"selection": 2})
    with pytest.raises(ValueError, match="selection"):
        await src.list_open()


async def test_hayotbirja_site_down_gives_up_fast(tmp_path):
    src = _Sections(tmp_path, {"tender": 5, "selection": 5})
    with pytest.raises(ValueError):
        await src.list_open()
    assert src.calls == ["tender", "selection"]  # no second round while nothing loads


def test_hayotbirja_opens_only_cards_with_generic_titles():
    def lot(title):
        return hb.LotSummary(source="hayotbirja", external_id="1", url="u", title=title)

    for t in ["ПГЗ из Агенства №419022", "Takliflar so‘rovi", "Энг яхши таклифларни танлаб олиш", "Lot 9802393",
              "Многолотовая процедура"]:
        assert hb.HayotbirjaSource.needs_detail(lot(t)), t
    for t in ["Труба водосточная металлическая, Отвод", "Махсус техникани таъмирлаш хизмати"]:
        assert not hb.HayotbirjaSource.needs_detail(lot(t)), t


def test_hayotbirja_generic_title_is_marked_and_replaced(tmp_path):
    cards = load("hayotbirja/list_tender.json")
    pgz = next(r for r in cards if r["id"] == "9761468")
    lot, _ = hb.parse_card("tender", pgz, NOW)
    assert lot.title.startswith("ПГЗ из Агенства") and lot.title_is_category  # card name wins when stored
    src = hb.HayotbirjaSource(browser=None, cache_dir=tmp_path)
    src.state.data["9761468"] = {"t": "Сунъий қон айланиши аппаратлари жамланмаси"}
    again = src._remembered(lot, NOW)
    assert again.title == "Сунъий қон айланиши аппаратлари жамланмаси" and not again.title_is_category


async def test_hayotbirja_list_is_reused_within_the_hour(tmp_path):
    src = _Sections(tmp_path, {})
    first = await src.list_open()
    calls = list(src.calls)
    again = await src.list_open()  # 20 minutes later: no new page loads
    assert src.calls == calls and {x.external_id for x in again} == {x.external_id for x in first}


async def test_hayotbirja_429_backs_off_and_keeps_the_last_list(tmp_path):
    src = _Sections(tmp_path, {})
    first = await src.list_open()
    src._listed_at = 0  # an hour later

    async def limited(section):
        src.calls.append(section)
        raise hb.RateLimited("429")

    src._section = limited
    kept = await src.list_open()
    assert len(kept) == len(first) and src._limited()
    n = len(src.calls)
    src._listed_at = 0
    await src.list_open()  # during the back-off the site is not asked at all
    assert len(src.calls) == n
    with pytest.raises(hb.RateLimited):
        await src.get_lot("1")  # a card not in the cache waits too


async def test_hayotbirja_backoff_survives_a_restart(tmp_path):
    src = _Sections(tmp_path, {})
    src._hit_limit()
    again = _Sections(tmp_path, {})  # the bot was restarted
    assert again._limited()
    with pytest.raises(ValueError):
        await again.list_open()  # no list kept in memory yet, and the site is not asked
    assert again.calls == []
