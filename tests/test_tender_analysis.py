"""Gemini analysis (mocked) + document extraction + how verdicts change notifications."""

import shutil
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.db.models import LLMCall, Lot, LotStatus
from app.tenders.analyzer import TenderAnalysis, TenderAnalyzer, postprocess
from app.tenders.documents import extract_lot_documents, text_quality_ok
from tests.test_tender_service import NOW, make_world


def analysis(verdict="QATNASHAMIZ", **kw) -> TenderAnalysis:
    base = dict(
        is_it_lot=True, summary_uz="Dispetcherlik platformasi va mobil ilova", fit_score=78, verdict=verdict,
        verdict_reason_uz="Mos keladi", win_chance_percent=[40, 30], blockers=[], risks=["TZ mavjud tizimga yozilgan."],
        contradictions=["kafolat 6 oy vs 5 yil"], estimated_tech_score=[55, 65], pass_mark=60,
        deposit_within_limit=True, hardware_or_server_required="yo'q",
        questions_for_customer_uz=["Server kim tomonidan beriladi?"],
        key_dates={"submission_deadline": "19.11.2026 19:06", "questions_deadline_estimate": "noma'lum"},
    )
    base.update(kw)
    return TenderAnalysis.model_validate(base)


class FakeGemini:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.uploaded, self.deleted = [], []

    def model_for(self, role):
        return "fake-flash"

    async def upload_file(self, path, mime_type):
        self.uploaded.append(path)
        return type("F", (), {"name": "files/abc"})()

    async def delete_file(self, name):
        self.deleted.append(name)

    async def generate_json(self, contents, schema, **kw):
        self.calls.append((contents, kw))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def with_analyzer(settings, sessionmaker, results):
    svc, source, sender = make_world(settings, sessionmaker)
    gem = FakeGemini(results)
    svc.analyzer = TenderAnalyzer(gem, settings, sessionmaker)
    return svc, source, sender, gem


def test_postprocess_normalizes():
    data = postprocess(analysis(is_it_lot=False, fit_score=140, win_chance_percent=[40, 30]))
    assert data["verdict"] == "YO'Q"  # not IT → always YO'Q
    assert data["fit_score"] == 100
    assert data["win_chance_percent"] == [30, 40]


async def test_good_lot_card_shows_verdict(settings, sessionmaker):
    svc, _, sender, gem = with_analyzer(settings, sessionmaker, [analysis()] * 10)
    await svc.run_scan(NOW)
    card = next(m for m in sender.messages if "523525" in m)
    assert "✅ QATNASHAMIZ (fit 78/100, yutish ≈ 30–40%)" in card
    assert "Dispetcherlik platformasi" in card and "⚠️ Risklar: TZ mavjud tizimga yozilgan" in card

    prompt, kw = next(c for c in gem.calls if "523525" in c[0][0])
    text = prompt[0]
    assert kw["purpose"] == "tender_analysis"
    assert "KOMPANIYA PROFILI" in text and "QUTLIYA" in text and "zakalat_limiti_usd: 1000" in text
    assert "<untrusted-" in text and "CRM tizimi uchun TZ yozish" in text
    assert "Ignore" not in kw["system"] and "noma'lum" in kw["system"]


async def test_no_lots_are_announced_individually_but_appear_in_report(settings, sessionmaker):
    svc, _, sender, _ = with_analyzer(settings, sessionmaker, [analysis("YO'Q", fit_score=5)] * 10)
    await svc.run_scan(NOW)
    assert not any("Yangi IT lot" in m for m in sender.messages)
    async with sessionmaker() as s:
        lot = await s.scalar(select(Lot).where(Lot.external_id == "523525"))
        assert lot.status == LotStatus.NOTIFIED and lot.verdict == "YO'Q"
    report = await svc.daily_report(NOW + timedelta(hours=1))
    assert "❌ Mos emas: " in report and "523525" in report


async def test_gemini_failure_marks_unrated_and_still_notifies(settings, sessionmaker):
    svc, _, sender, _ = with_analyzer(settings, sessionmaker, [RuntimeError("503 overloaded")] * 10)
    await svc.run_scan(NOW)
    card = next(m for m in sender.messages if "523525" in m)
    assert "⚠️ baholanmagan" in card
    _, full = await svc.lot_text("523525")
    assert "AI baholamadi" in full and "/analyze 523525" in full


async def test_daily_request_limit(settings, sessionmaker):
    settings.config.tenders.analysis.max_requests_per_day = 1
    svc, _, sender, gem = with_analyzer(settings, sessionmaker, [analysis()] * 10)
    async with sessionmaker() as s:
        s.add(LLMCall(model="m", purpose="tender_analysis"))
        await s.commit()
    await svc.run_scan(NOW)
    assert gem.calls == []
    assert any("⚠️ baholanmagan" in m for m in sender.messages)


async def test_backlog_is_analysed_quietly_with_one_summary(settings, sessionmaker):
    svc, _, sender = make_world(settings, sessionmaker)
    await svc.run_scan(NOW)  # step-2 behaviour: announced without AI
    announced = len(sender.messages)
    svc.analyzer = TenderAnalyzer(FakeGemini([analysis("SHARTLI")] * 10), settings, sessionmaker)
    await svc.run_scan(NOW + timedelta(minutes=30))
    new = sender.messages[announced:]
    assert len(new) == 1 and "AI bilan tahlil qilindi" in new[0] and "🟡" in new[0]
    async with sessionmaker() as s:
        assert (await s.scalar(select(Lot).where(Lot.external_id == "523525"))).verdict == "SHARTLI"


async def test_scanned_tz_is_uploaded_then_deleted(settings, sessionmaker, tmp_path):
    svc, _, _, gem = with_analyzer(settings, sessionmaker, [analysis()] * 10)
    blank = tmp_path / "scan.pdf"
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(595, 842)
    w.write(str(blank))

    async def fake_download(file, dest, *, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(blank, dest)
        return dest.stat().st_size

    svc.sources["etender"].download = fake_download
    await svc.run_scan(NOW)
    assert gem.uploaded and gem.deleted == ["files/abc"] * len(gem.uploaded)
    contents, _ = next(c for c in gem.calls if "523525" in c[0][0])
    assert len(contents) == 2 and "skaner PDF" in contents[0]


def test_documents_docx_archive_and_budget(tmp_path):
    docx = tmp_path / "tz.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml",
                   "<w:document><w:p><w:t>Texnik topshiriq: CRM</w:t></w:p><w:p><w:t>2-bo&apos;lim</w:t></w:p></w:document>")
    archive = tmp_path / "tz.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(docx, "1-Texnik_topshiriq.docx")
        z.writestr("notes.xyz", "?")
    files = [
        {"kind": "contract_draft", "label": "Shartnoma loyihasi", "local": str(docx)},
        {"kind": "tz", "label": "Texnik topshiriq", "local": str(archive)},
        {"kind": "expertise", "label": "Ekspertiza", "local": str(docx)},
        {"kind": "tech_doc", "label": "Texnik hujjatlar", "local": None},
    ]
    docs = extract_lot_documents(files, tmp_path / "work", max_chars=30,
                                 scan_pdf_max_bytes=10**7, scan_pdf_max_pages=100)
    labels = [d.label for d in docs]
    assert labels[0].startswith("Texnik topshiriq › 1-Texnik_topshiriq.docx")  # TZ first, from archive
    assert "Ekspertiza" not in labels
    assert docs[0].text.startswith("Texnik topshiriq: CRM\n2-bo'lim")
    assert any(d.mode == "skipped" and "qo'llab" in d.note for d in docs)  # .xyz
    assert any(d.label == "Texnik hujjatlar" and "yuklab olinmagan" in d.note for d in docs)
    assert sum(len(d.text) for d in docs) <= 30  # shared budget
    assert (tmp_path / "work" / "extracted" / "tz").is_dir()  # own folder per archive


def test_text_quality():
    assert not text_quality_ok("")
    assert not text_quality_ok("YTBEP}I(AAK) .(upenrop Ercpo COT,IIACOBAHO tlepBr,rii 3aMecr " * 40)
    assert text_quality_ok("Texnik topshiriq. Dasturiy ta'minot ishlab chiqish talablari. " * 20)


@pytest.fixture(autouse=True)
def _cleanup_lots(settings):
    yield
    shutil.rmtree(Path(settings.secrets.data_dir), ignore_errors=True)
