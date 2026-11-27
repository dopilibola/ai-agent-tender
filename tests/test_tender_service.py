"""End-to-end tender pipeline with a fake source and a fake Telegram sender."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Lot, LotStatus, SeenLot
from app.tenders.schemas import LotFile, LotRecord, LotSummary
from app.tenders.service import TenderService
from app.tenders.sources.etender import parse_list_row, parse_trade
from tests.conftest import load_fixture

NOW = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)  # 11:00 Tashkent, before every fixture deadline


class FakeSource:
    name = "etender"
    title = "etender.uzex.uz"

    def __init__(self, summaries: list[LotSummary], records: dict[str, LotRecord]):
        self.summaries = summaries
        self.records = records
        self.detail_calls: list[str] = []
        self.downloads: list[str] = []
        self.fail = False

    async def list_open(self):
        if self.fail:
            raise ConnectionError("etender down")
        return list(self.summaries)

    async def get_lot(self, external_id):
        self.detail_calls.append(external_id)
        return self.records[external_id]

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        self.downloads.append(file.kind)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake")
        return 13

    async def aclose(self):
        pass


class FakeSender:
    def __init__(self):
        self.messages: list[str] = []
        self.down = False

    async def send(self, text, reply_markup=None, **kw):
        if self.down:
            raise RuntimeError("telegram down")
        self.messages.append(text)


class FixedRate:
    async def get(self):
        return 12_000.0


def make_world(settings, sessionmaker):
    rows = load_fixture("etender", "trade_list.json")
    summaries = [parse_list_row(r) for r in rows]
    records = {
        "523525": parse_trade(load_fixture("etender", "trade_523525.json")),
        "524034": parse_trade(load_fixture("etender", "trade_524034.json")),
    }
    # Other listed lots get a minimal non-IT card if they are ever opened.
    for s in summaries:
        records.setdefault(
            s.external_id, LotRecord(**s.model_dump(), description="Oddiy xizmat", is_open=True)
        )
    source = FakeSource(summaries, records)
    sender = FakeSender()
    svc = TenderService(settings, sessionmaker, {"etender": source}, sender, fx=FixedRate())
    return svc, source, sender


async def lots(sessionmaker):
    async with sessionmaker() as s:
        return {lot.external_id: lot for lot in (await s.scalars(select(Lot))).all()}


async def test_scan_finds_it_lot_and_notifies_once(settings, sessionmaker):
    svc, source, sender = make_world(settings, sessionmaker)
    await svc.run_scan(NOW)

    found = await lots(sessionmaker)
    assert "523525" in found  # CRM tizimi uchun TZ yozish
    assert "524034" not in found  # transport service
    assert "522909" not in found  # Muzrabot
    crm = found["523525"]
    assert crm.status == LotStatus.NOTIFIED
    assert crm.usd_rate == 12_000.0
    assert all(f["local"] and Path(f["local"]).exists() for f in crm.files)
    assert Path(crm.files[0]["local"]).parent.name == "523525"  # data/lots/<raqam>/

    cards = [m for m in sender.messages if "Yangi IT lot" in m]
    assert len(cards) >= 1
    card = next(m for m in cards if "523525" in m)
    assert "1.7 mlrd so'm" in card and "≈ $141,667" in card
    assert "Zakalat 1% = 17 mln (≈ $1,417)" in card and "❌ limitdan oshadi" in card
    assert "Narx 30% / texnik 70% | O'tish bali 15" in card
    assert "17.11.2026 16:13" in card

    # second scan: nothing new, no duplicate messages, no repeated detail calls
    before, calls = len(sender.messages), len(source.detail_calls)
    await svc.run_scan(NOW + timedelta(minutes=30))
    assert len(sender.messages) == before
    assert len(source.detail_calls) == calls


async def test_hardware_names_are_not_opened(settings, sessionmaker):
    svc, source, _ = make_world(settings, sessionmaker)
    source.summaries.append(
        LotSummary(source="etender", external_id="999", url="u", title="Kompyuter jihozlari xaridi",
                   deadline_at=NOW + timedelta(days=3))
    )
    await svc.run_scan(NOW)
    assert "999" not in source.detail_calls
    async with sessionmaker() as s:
        seen = await s.scalar(select(SeenLot).where(SeenLot.external_id == "999"))
        assert seen.detail_checked


async def test_deadline_extension_sends_change(settings, sessionmaker):
    svc, source, sender = make_world(settings, sessionmaker)
    await svc.run_scan(NOW)
    await svc.set_status("523525", LotStatus.BIDDING)

    new_deadline = datetime(2026, 11, 24, 11, 13, 35, tzinfo=UTC)
    for s in source.summaries:
        if s.external_id == "523525":
            s.deadline_at = new_deadline
    source.records["523525"] = source.records["523525"].model_copy(update={"deadline_at": new_deadline})

    sender.messages.clear()
    await svc.run_scan(NOW + timedelta(hours=1))
    assert len(sender.messages) == 1
    msg = sender.messages[0]
    assert "🔄 O'zgarish" in msg and "uzaytirildi" in msg and "24.11.2026 16:13" in msg


async def test_cancelled_lot_is_closed_with_message(settings, sessionmaker):
    svc, source, sender = make_world(settings, sessionmaker)
    await svc.run_scan(NOW)
    source.summaries = [s for s in source.summaries if s.external_id != "523525"]
    source.records["523525"] = source.records["523525"].model_copy(
        update={"is_open": False, "platform_status": "Bekor qilindi"}
    )
    sender.messages.clear()
    await svc.run_scan(NOW + timedelta(hours=1))
    assert (await lots(sessionmaker))["523525"].status == LotStatus.CLOSED
    assert any("Bekor qilindi" in m for m in sender.messages)


async def test_source_outage_alerts_once_and_recovery(settings, sessionmaker):
    svc, source, sender = make_world(settings, sessionmaker)
    source.fail = True
    await svc.run_scan(NOW)
    await svc.run_scan(NOW + timedelta(minutes=30))
    alerts = [m for m in sender.messages if "ishlamayapti" in m]
    assert len(alerts) == 1
    source.fail = False
    await svc.run_scan(NOW + timedelta(hours=1))
    assert any("yana ishlayapti" in m for m in sender.messages)


async def test_telegram_outage_retries_notification(settings, sessionmaker):
    svc, _, sender = make_world(settings, sessionmaker)
    sender.down = True
    await svc.run_scan(NOW)
    assert (await lots(sessionmaker))["523525"].status == LotStatus.NEW
    sender.down = False
    await svc.run_scan(NOW + timedelta(minutes=30))
    assert (await lots(sessionmaker))["523525"].status == LotStatus.NOTIFIED
    assert sum("523525" in m for m in sender.messages) == 1


async def test_reminders_for_bidding_lot(settings, sessionmaker):
    svc, _, sender = make_world(settings, sessionmaker)
    await svc.run_scan(NOW)
    await svc.set_status("523525", LotStatus.BIDDING)
    deadline = datetime(2026, 11, 17, 11, 13, 35, tzinfo=UTC)

    sender.messages.clear()
    # 23 h before: questions window passed, deposit (3 workdays) due, 24 h due → ONE combined message
    await svc.run_reminders(deadline - timedelta(hours=23))
    assert len(sender.messages) == 1
    assert "Zakalatni o'tkazing" in sender.messages[0] and "Topshirishga" in sender.messages[0]
    # same moment again: nothing
    await svc.run_reminders(deadline - timedelta(hours=22))
    assert len(sender.messages) == 1
    # 3 h before: one more
    await svc.run_reminders(deadline - timedelta(hours=2, minutes=50))
    assert len(sender.messages) == 2
    # after deadline: closed, final message
    await svc.run_reminders(deadline + timedelta(minutes=1))
    assert (await lots(sessionmaker))["523525"].status == LotStatus.CLOSED
    assert "muddati tugadi" in sender.messages[-1]


async def test_commands_list_status_report(settings, sessionmaker):
    svc, _, _ = make_world(settings, sessionmaker)
    await svc.run_scan(NOW)
    open_lots = await svc.open_lots(NOW)
    assert any(lot.external_id == "523525" for lot in open_lots)

    lot, text = await svc.lot_text("523525")
    assert lot and "Texnik mezonlar" in text and "avans 10%" in text
    assert (await svc.lot_text("000"))[0] is None

    assert (await svc.set_status("523525", LotStatus.IGNORED)).status == LotStatus.IGNORED
    assert "etender.uzex.uz: ✅" in await svc.status_text(NOW)
    report = await svc.daily_report(NOW + timedelta(hours=1))
    assert "Yangi IT lotlar (24 soat)" in report and "523525" in report


@pytest.mark.parametrize("hours_before,expected", [(30, set()), (23, {"deposit", "h24"}), (2, {"deposit", "h24", "h3"})])
def test_due_reminders(settings, sessionmaker, hours_before, expected):
    svc, _, _ = make_world(settings, sessionmaker)
    deadline = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)  # Friday
    lot = Lot(deadline_at=deadline, reminders_sent=[])
    now = deadline - timedelta(hours=hours_before)
    # deposit point = 3 workdays before Friday = Tuesday → already due 30 h before as well
    got = set(svc.due_reminders(lot, now))
    assert got - {"deposit"} == expected - {"deposit"}
    assert "deposit" in got


async def test_one_broken_card_does_not_stop_the_scan(settings, sessionmaker):
    """A card that will not open (slow site page) is skipped and tried again next scan; the rest are read."""
    svc, source, sender = make_world(settings, sessionmaker)
    real = source.get_lot
    broken = {"523525"}

    async def flaky(external_id):
        if external_id in broken:
            source.detail_calls.append(external_id)
            raise ValueError("karta o'qilmadi")
        return await real(external_id)

    source.get_lot = flaky
    await svc.run_scan(NOW)
    assert "523525" not in await lots(sessionmaker)
    async with sessionmaker() as s:
        seen = (await s.scalars(select(SeenLot).where(SeenLot.external_id == "523525"))).one()
        assert not seen.detail_checked  # stays in the queue
        health = await svc.health()
    assert not health.get("etender", {}).get("alerted")

    broken.clear()  # the page loads on the next scan
    await svc.run_scan(NOW + timedelta(minutes=30))
    assert (await lots(sessionmaker))["523525"].status == LotStatus.NOTIFIED
