"""💼 Freelance pipeline with a fake connector and mocked Gemini (no network)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.connectors.base import JobListing
from app.connectors.freelancer_com import parse_project
from app.core.approvals import ApprovalRequired, consume_approval
from app.core.states import JobStatus as S
from app.db.models import Approval, ApprovalKind, ApprovalStatus, Job, Proposal
from app.pipeline.ai import JobScore, Milestone, ProposalDraft, clamp_price
from app.pipeline.filters import hard_filter
from app.pipeline.service import FreelanceService

NOW = datetime(2026, 11, 17, 6, 0, tzinfo=UTC)


def listing(i: int, **kw) -> JobListing:
    base = dict(platform="freelancer_com", external_id=str(i), url=f"https://www.freelancer.com/projects/x-{i}",
                title=f"Telegram bot with Django admin #{i}", description="Need a Telegram bot + Django admin panel.",
                budget_min=100, budget_max=250, currency="USD", usd_rate=1.0, proposals_count=5,
                posted_at=NOW - timedelta(hours=2), language="en", skills=["Python", "Django"])
    base.update(kw)
    return JobListing(**base)


class FakeConnector:
    name, title, can_submit = "freelancer_com", "Freelancer.com", False

    def __init__(self, items):
        self.items = items
        self.fail = False

    async def fetch_jobs(self):
        if self.fail:
            raise ConnectionError("down")
        return list(self.items)

    async def aclose(self):
        pass


class Sender:
    def __init__(self):
        self.messages = []

    async def send(self, text, reply_markup=None, **kw):
        self.messages.append((text, reply_markup))


def make(settings, sessionmaker, items, *, score=None, fail_score=False):
    sender = Sender()
    svc = FreelanceService(settings, sessionmaker, {"freelancer_com": FakeConnector(items)}, gemini=None, sender=sender)
    calls = {"score": 0, "write": []}

    async def fake_score(gemini, job, profile, min_rate):
        calls["score"] += 1
        if fail_score:
            raise RuntimeError("503")
        return score or JobScore(fit=85, win_probability=35, effort_hours=6, risk_flags=[],
                                 summary_uz="Mijozga bot kerak. Byudjet yetarli.", recommended=True)

    async def fake_write(gemini, job, profile, *, current=None, instruction=None):
        calls["write"].append(instruction)
        text = "Hi! You need a Telegram bot with an admin panel. Plan: 1) ... Question?"
        if instruction:
            text = f"Shorter version ({instruction})"
        return ProposalDraft(text=text, price=clamp_price(300, job), timeline_days=3,
                             milestones=[Milestone(name="MVP", amount=250)]), "proposal@1"

    svc.score, svc.write = fake_score, fake_write
    return svc, sender, calls


async def jobs_by_ext(sessionmaker):
    async with sessionmaker() as s:
        return {j.external_id: j for j in (await s.scalars(select(Job))).all()}


async def test_good_job_becomes_card_once(settings, sessionmaker):
    svc, sender, calls = make(settings, sessionmaker, [listing(1)])
    st = await svc.run_cycle(NOW)
    assert (st.fetched, st.new, st.scored, st.drafted) == (1, 1, 1, 1)
    job = (await jobs_by_ext(sessionmaker))["1"]
    assert job.status == S.AWAITING_APPROVAL and job.quote == 250  # clamped to budget max
    text, markup = sender.messages[0]
    assert "💼 🟢 <b>Yangi ish</b> | Freelancer.com | Fit 85 · Yutish 35%" in text
    assert "Narxim: $250" in text and "Muddat 3 kun" in text
    assert markup is not None
    # second cycle: same listing, nothing new
    await svc.run_cycle(NOW + timedelta(minutes=10))
    assert len(sender.messages) == 1 and calls["score"] == 1


@pytest.mark.parametrize("item,reason", [
    (listing(2, proposals_count=60), "takliflar ko'p"),
    (listing(3, budget_max=5, budget_min=2), "byudjet kichik"),
    (listing(4, posted_at=NOW - timedelta(hours=100)), "soatdan eski"),
    (listing(5, language="de"), "til"),
    (listing(6, budget_type="hourly", budget_max=4, budget_min=2), "soatbay stavka past"),
])
def test_hard_filters(settings, item, reason):
    got = hard_filter(item, settings.config, settings.profile, settings.config.platforms["freelancer_com"], NOW)
    assert got and reason in got


async def test_filtered_jobs_never_reach_gemini(settings, sessionmaker):
    svc, sender, calls = make(settings, sessionmaker, [listing(2, proposals_count=60)])
    st = await svc.run_cycle(NOW)
    assert st.filtered == 1 and calls["score"] == 0 and sender.messages == []
    assert (await jobs_by_ext(sessionmaker))["2"].status == S.SKIPPED


@pytest.mark.parametrize("score", [
    JobScore(fit=90, win_probability=50, effort_hours=2, risk_flags=["off_platform_payment"], summary_uz="x", recommended=True),
    JobScore(fit=40, win_probability=10, effort_hours=2, risk_flags=[], summary_uz="x", recommended=True),
    JobScore(fit=90, win_probability=10, effort_hours=2, risk_flags=[], summary_uz="x", recommended=False),
])
async def test_ai_skips_bad_jobs_quietly(settings, sessionmaker, score):
    svc, sender, calls = make(settings, sessionmaker, [listing(7)], score=score)
    await svc.run_cycle(NOW)
    assert (await jobs_by_ext(sessionmaker))["7"].status == S.SKIPPED
    assert sender.messages == [] and calls["write"] == []


async def test_scoring_failure_retries_next_cycle(settings, sessionmaker):
    svc, sender, calls = make(settings, sessionmaker, [listing(8)], fail_score=True)
    await svc.run_cycle(NOW)
    assert (await jobs_by_ext(sessionmaker))["8"].status == S.FOUND


async def test_approve_requires_owner_and_is_single_use(settings, sessionmaker):
    svc, _, _ = make(settings, sessionmaker, [listing(9)])
    await svc.run_cycle(NOW)
    job = (await jobs_by_ext(sessionmaker))["9"]
    with pytest.raises(PermissionError):
        await svc.approve(job.id, user_id=999)
    assert await svc.approve(job.id, user_id=settings.owner_chat_id) == "manual"  # no token → paste manually
    assert (await jobs_by_ext(sessionmaker))["9"].status == S.APPROVED
    async with sessionmaker() as s:
        proposal = await s.scalar(select(Proposal).where(Proposal.job_id == job.id))
        await consume_approval(s, kind=ApprovalKind.SUBMIT_PROPOSAL, target_type="proposal", target_id=proposal.id)
        with pytest.raises(ApprovalRequired):
            await consume_approval(s, kind=ApprovalKind.SUBMIT_PROPOSAL, target_type="proposal", target_id=proposal.id)


async def test_edit_and_rewrite_create_new_versions(settings, sessionmaker):
    svc, _, calls = make(settings, sessionmaker, [listing(10)])
    await svc.run_cycle(NOW)
    job_id = (await jobs_by_ext(sessionmaker))["10"].id
    job, p2 = await svc.replace_text(job_id, "My own text")
    assert p2.version == 2 and p2.text == "My own text" and job.status == S.AWAITING_APPROVAL
    job, p3 = await svc.rewrite(job_id, "shorter, mention Django")
    assert p3.version == 3 and "shorter" in p3.text and calls["write"][-1] == "shorter, mention Django"
    async with sessionmaker() as s:
        statuses = [a.status for a in (await s.scalars(select(Approval).where(Approval.job_id == job_id)
                                                       .order_by(Approval.id))).all()]
    assert statuses == [ApprovalStatus.REJECTED, ApprovalStatus.REJECTED, ApprovalStatus.PENDING]
    assert await svc.approve(job_id, settings.owner_chat_id) == "manual"


async def test_skip_button(settings, sessionmaker):
    svc, _, _ = make(settings, sessionmaker, [listing(11)])
    await svc.run_cycle(NOW)
    job_id = (await jobs_by_ext(sessionmaker))["11"].id
    await svc.skip(job_id, settings.owner_chat_id)
    assert (await jobs_by_ext(sessionmaker))["11"].status == S.SKIPPED
    assert await svc.approve(job_id, settings.owner_chat_id) != "manual"


async def test_daily_proposal_cap(settings, sessionmaker):
    settings.config.max_proposals_per_day = 2
    svc, sender, _ = make(settings, sessionmaker, [listing(i) for i in range(20, 25)])
    await svc.run_cycle(NOW)
    assert len(sender.messages) == 2
    assert sum(j.status == S.SCORED for j in (await jobs_by_ext(sessionmaker)).values()) == 3


async def test_connector_outage_alerts_once(settings, sessionmaker):
    svc, sender, _ = make(settings, sessionmaker, [])
    svc.connectors["freelancer_com"].fail = True
    await svc.run_cycle(NOW, force=True)
    await svc.run_cycle(NOW, force=True)
    assert sum("ulanmadi" in t for t, _ in sender.messages) == 1
    svc.connectors["freelancer_com"].fail = False
    await svc.run_cycle(NOW, force=True)
    assert any("yana ishlayapti" in t for t, _ in sender.messages)
    assert "Freelancer.com: ✅" in await svc.status_text(NOW)


def test_parse_freelancer_project():
    job = parse_project({
        "id": 40821290, "title": " Sports  Betting ", "seo_url": "api-developmet/Sports-Betting",
        "type": "fixed", "budget": {"minimum": 12500.0, "maximum": 37500.0},
        "currency": {"code": "INR", "exchange_rate": 0.010651}, "bid_stats": {"bid_count": 49},
        "time_submitted": 1794855382, "language": "en", "description": "desc",
        "jobs": [{"name": "Python"}, {"name": "Django"}],
    })
    assert job.url == "https://www.freelancer.com/projects/api-developmet/Sports-Betting"
    assert job.title == "Sports Betting" and job.currency == "INR"
    assert job.budget_usd == pytest.approx(399.41, abs=0.01)
    assert job.proposals_count == 49 and job.skills == ["Python", "Django"]
