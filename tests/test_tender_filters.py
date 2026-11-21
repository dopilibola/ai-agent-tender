"""Keyword filter, checked against real etender lot names (fixtures + names seen on 16.11.2026)."""

import pytest

from app.tenders.filters import KeywordFilter, compile_term, normalize
from tests.conftest import load_fixture


@pytest.fixture
def kf(settings):
    return KeywordFilter(settings.config.tenders)


@pytest.mark.parametrize(
    "title",
    [
        "CRM tizimi uchun TZ yozish xizmati",
        "Kiber tahdidlarni kengaytirilgan aniqlash va ularga javob berish dasturiy ta’minotini yetkazib berish",
        "Bankning sho‘ba korxonalari faoliyatini nazorat qilishda markazlashtirilgan hisobot-monitoring tizimini joriy etish",
        "Korxona uchun veb-saytni ishlab chiqish",
        "Разработка информационной системы учета",
        "Мобильное приложение для водителей и диспетчерская платформа",
        "Ахборот тизимини яратиш хизмати",
        "1С: Бухгалтерия ни жорий этиш",
        "Telegram bot va elektron navbat platformasi",
    ],
)
def test_it_lots_pass(kf, title):
    m = kf.match(title)
    assert m.is_it, m.reason


@pytest.mark.parametrize(
    "title",
    [
        "Muzrabot tuman dezenfeksiya",  # "bot" inside a word
        "Buxoro MGQBB TTXKM ombori uchun mezzanin platformasini yetkazib berish va o‘rnatish",
        "Kompyuter jihozlari va ofis texnikalarini xarid qilish bo'yicha xarid qilish",
        "Maktabgacha va maktab ta’limi vazirligi markaziy apparatining Axborot hizmati boshqarmasi uchun zarur jihozlar",
        "Ofis texnikasi, tarmoq uskunalariga texnik xizmat ko‘rsatish hamda kartridjlarni qayta tiklash",
        "Umumta’lim maktablari uchun elektron doskalarni xarid qilish",  # 1 weak word only
        "Windows litsenziyalarini sotib olish (dasturiy ta'minot)",  # licence resale
        "Навбахор туман ободонлаштириш бошкармаси транспорт хизмати",
    ],
)
def test_non_it_lots_rejected(kf, title):
    m = kf.match(title)
    assert not m.is_it, m.reason


def test_override_beats_exclusion(kf):
    assert kf.match("Kompyuter sinfi uchun dasturiy ta'minot ishlab chiqish").is_it


def test_description_is_searched(kf):
    assert kf.match("Xizmat ko'rsatish", "Texnik topshiriq: korporativ portal va CRM integratsiyasi").is_it


def test_normalize_and_terms():
    assert normalize("Dasturiy ta‘minot — «CRM»") == "dasturiy ta'minot crm"
    assert compile_term("bot$").search("bot va sayt")
    assert not compile_term("bot$").search("botanika")
    assert not compile_term("sayt").search("muzsayt")
    assert compile_term("veb sayt").search(normalize("veb-saytini"))


def test_fixture_listing(kf):
    rows = {str(r["id"]): r["name"] for r in load_fixture("etender", "trade_list.json")}
    hits = {k for k, name in rows.items() if kf.match(name).is_it}
    assert {"523525", "524100", "524795"} <= hits
    assert not hits & {"522909", "524035", "524034"}  # Muzrabot, mezzanin, transport


@pytest.mark.parametrize(
    "title, description",
    [
        # Real false positives seen on 21.11.2026: one concept in two scripts used to count as 2 weak words.
        ("Bo'yoqlash tsexining sovutish tizimini modernizatsiya qilish", "Тизим ... tizim"),
        ("Master-rejani ishlab chiqish xizmati", "Мастер-режани ишлаб чиқиш"),
        ("Токарно-винторезный станок", "Электронная площадка, elektron do'kon"),
        ("Данғара туманидаги коллекторини тизимли таъмирлаш", "tizimli ta'mirlash, систем"),
    ],
)
def test_same_concept_in_two_scripts_is_one_word(kf, title, description):
    m = kf.match(title, description)
    assert not m.is_it, m.reason


def test_two_different_concepts_still_pass(kf):
    assert kf.match("Monitoring tizimini joriy etish").is_it
    assert kf.match("Telegram bot va elektron navbat platformasi").is_it


def test_is_it_lot_uses_ai_and_title_rescue(kf):
    from types import SimpleNamespace as L

    from app.tenders.filters import is_it_lot

    assert is_it_lot(L(title="Токарный станок", analysis=None), kf)  # not analysed yet → keyword stage decided
    assert not is_it_lot(L(title="Токарный станок", analysis={"is_it_lot": False}), kf)
    assert is_it_lot(L(title="MedERP", analysis={"is_it_lot": True}), kf)
    # AI said "not IT", but the title is clearly IT → keep it
    assert is_it_lot(L(title="Услуга по технической поддержке информационных технологий",
                       analysis={"is_it_lot": False}), kf)
