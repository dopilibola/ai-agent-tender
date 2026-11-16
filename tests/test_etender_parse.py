"""Parsing of real etender API responses saved on 16.11.2026 (tests/fixtures/etender)."""

from datetime import UTC, datetime

from app.tenders.fx import parse_cbu
from app.tenders.sources.etender import parse_list_row, parse_trade
from app.tenders.timeutil import fmt_local, parse_local, remaining_uz, subtract_workdays
from tests.conftest import load_fixture


def test_list_row():
    row = next(r for r in load_fixture("etender", "trade_list.json") if r["id"] == 523525)
    lot = parse_list_row(row)
    assert lot.external_id == "523525"
    assert lot.url == "https://etender.uzex.uz/lot/523525"
    assert lot.title == "CRM tizimi uchun TZ yozish xizmati"
    assert lot.start_cost == 1_700_000_000
    assert lot.currency == "UZS"
    # 2026-11-17T16:13:35 Tashkent == 11:13:35 UTC
    assert lot.deadline_at == datetime(2026, 11, 17, 11, 13, 35, tzinfo=UTC)


def test_trade_card_fields():
    rec = parse_trade(load_fixture("etender", "trade_523525.json"))
    assert rec.is_open and rec.platform_status == "Eʼlon qilindi"
    assert rec.pledge_percent == 1.0 and rec.pledge_amount == 17_000_000
    assert rec.advance_percent == 10.0
    assert (rec.tech_weight, rec.price_weight, rec.pass_mark) == (70.0, 30.0, 15)
    assert rec.valuation_method == "Ball usuli"
    assert rec.languages == ["ru", "uz"]
    assert rec.financing_source == "O'z xisobidan"
    assert rec.warranty == "3 oy"
    assert len(rec.criteria) == 4 and sum(c.max_score for c in rec.criteria) == 100
    kinds = {f.kind for f in rec.files}
    assert {"tz", "tech_doc"} <= kinds
    assert fmt_local(rec.deadline_at) == "17.11.2026 16:13"


def test_tender_type_lot():
    rec = parse_trade(load_fixture("etender", "trade_522693.json"))
    assert rec.lot_type == "Tender"
    assert rec.pledge_percent == 3.0
    assert rec.warranty == "1 yil"
    assert rec.execution_days == 60
    assert rec.qualification  # js_qualification_fields parsed


def test_time_helpers():
    deadline = parse_local("2026-11-19T19:06:00")
    now = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)  # 12:00 Tashkent
    assert remaining_uz(deadline, now) == "3 kun 7 soat qoldi"
    # 19.11.2026 is a Thursday → 3 working days before is Monday 16.11
    assert fmt_local(subtract_workdays(deadline, 3)) == "16.11.2026 19:06"
    # across a weekend: Monday 23.11 → Wednesday 18.11
    assert fmt_local(subtract_workdays(parse_local("2026-11-23T10:00:00"), 3)) == "18.11.2026 10:00"


def test_cbu_parse():
    rate, date = parse_cbu([{"Ccy": "EUR", "Rate": "1", "Nominal": "1"},
                            {"Ccy": "USD", "Rate": "12014.37", "Nominal": "1", "Date": "14.11.2026"}])
    assert rate == 12014.37 and date == "14.11.2026"
