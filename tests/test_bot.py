from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.bot.cards import ago_uz, format_job_card, money
from app.bot.middleware import OwnerOnlyMiddleware
from app.bot.notifier import split_text
from app.db.models import Job, Proposal


async def test_owner_only_middleware():
    mw = OwnerOnlyMiddleware(42)
    called = []

    async def handler(event, data):
        called.append(data["event_from_user"].id)
        return "ok"

    assert await mw(handler, object(), {"event_from_user": SimpleNamespace(id=7)}) is None
    assert await mw(handler, object(), {}) is None
    assert await mw(handler, object(), {"event_from_user": SimpleNamespace(id=42)}) == "ok"
    assert called == [42]


def test_job_card_matches_spec_layout():
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    job = Job(
        platform="truelancer", external_id="1", url="u", title="Python API <automation>",
        currency="INR", budget_max=1000, budget_usd=11, proposals_count=8,
        posted_at=now - timedelta(hours=2), fit=86, win_probability=40, risk_flags=[],
        summary_uz="Qisqa xulosa.",
    )
    p = Proposal(job_id=1, text="Hi", price=1000, currency="INR", deposit=400, timeline_days=2)
    card = format_job_card(job, p, now=now)
    assert card.startswith("💼 🟢 <b>Yangi ish</b> | Truelancer | Fit 86 · Yutish 40%")
    assert "byudjet ₹1,000 (~$11) · 8 ta taklif · 2 soat oldin" in card
    assert "&lt;automation&gt;" in card
    assert "Xavf:</b> yo'q" in card
    assert "Narxim: ₹1,000 · Avans ₹400 · Muddat 2 kun" in card


def test_helpers():
    assert money(12.5, "USD") == "$12.5"
    assert money(50, "XYZ") == "50 XYZ"
    now = datetime(2026, 1, 3, tzinfo=UTC)
    assert ago_uz(now - timedelta(minutes=5), now) == "5 daqiqa oldin"
    assert ago_uz(now - timedelta(days=3), now) == "3 kun oldin"


def test_split_text():
    text = "\n".join("x" * 100 for _ in range(100))
    parts = split_text(text, 1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts) == text
