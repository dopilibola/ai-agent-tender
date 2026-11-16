"""Gmail watcher: MIME parsing, Gemini classification (mocked), routing and UID bookkeeping."""

from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select

from app.connectors.email_watch import (
    EmailExtraction,
    EmailJob,
    EmailWatchConnector,
    RawEmail,
    job_external_id,
    parse_message,
)
from app.core.states import JobStatus
from app.db.models import Job
from app.pipeline.service import FreelanceService
from tests.test_freelance import make


def mime(subject: str, html_body: str, sender="Upwork <donotreply@upwork.com>") -> bytes:
    msg = MIMEMultipart("alternative")
    msg["From"], msg["Subject"] = sender, subject
    msg["Date"] = "Tue, 17 Nov 2026 03:00:00 +0000"
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg.as_bytes()


def test_parse_message_html_and_links():
    raw = mime("New job: Telegram bot",
               '<p>Hi&nbsp;Komronbek</p><a href="https://www.upwork.com/jobs/~01abc123def4567890">Telegram bot</a>'
               "<style>.x{}</style><br>Budget: $300")
    m = parse_message(42, raw)
    assert m.uid == 42 and m.sender == "donotreply@upwork.com" and m.subject == "New job: Telegram bot"
    assert "Budget: $300" in m.text and "<a" not in m.text and ".x{}" not in m.text
    assert m.links == ["https://www.upwork.com/jobs/~01abc123def4567890"]
    assert m.date == datetime(2026, 11, 17, 3, 0, tzinfo=UTC)


def test_upwork_job_id_from_url():
    j = EmailJob(title="x", url="https://www.upwork.com/jobs/Telegram-bot_~01abc123def4567890/")
    assert job_external_id("upwork", j) == "1abc123def4567890"
    assert len(job_external_id("fiverr", EmailJob(title="Brief", url=""))) == 16


class FakeFetcher:
    def __init__(self, mails):
        self.mails = mails
        self.calls = []

    def fetch_since(self, last_uid, limit):
        self.calls.append(last_uid)
        return [m for m in self.mails if last_uid is None or m.uid > last_uid][:limit]


class FakeGemini:
    def __init__(self, by_uid):
        self.by_uid = by_uid
        self.prompts = []

    async def generate_json(self, prompt, schema, **kw):
        self.prompts.append((prompt, kw))
        uid = int(prompt.split("UID=")[1].split()[0])
        r = self.by_uid[uid]
        if isinstance(r, Exception):
            raise r
        return r


def mail(uid, subject):
    return RawEmail(uid=uid, sender="noreply@upwork.com", subject=subject,
                    date=datetime(2026, 11, 17, 3, tzinfo=UTC), text=f"UID={uid} body", links=[])


ALERT = EmailExtraction(platform="upwork", kind="job_alert", summary_uz="Yangi ish", action_url="",
                        jobs=[EmailJob(title="Django REST API for CRM", url="https://www.upwork.com/jobs/~01aaa111bbb222ccc",
                                       description="Build API", budget_min=200, budget_max=400)])
MESSAGE = EmailExtraction(platform="upwork", kind="new_message", summary_uz="Mijoz javob yozdi",
                          action_url="https://www.upwork.com/messages/rooms/1", jobs=[])
PROMO = EmailExtraction(platform="upwork", kind="marketing", summary_uz="Reklama", action_url="", jobs=[])


async def test_routing_and_uid(sessionmaker):
    notes = []

    async def notify(text):
        notes.append(text)

    fetcher = FakeFetcher([mail(10, "job"), mail(11, "message"), mail(12, "promo")])
    gem = FakeGemini({10: ALERT, 11: MESSAGE, 12: PROMO})
    conn = EmailWatchConnector(fetcher, gem, sessionmaker, notify)
    jobs = await conn.fetch_jobs()
    assert [j.platform for j in jobs] == ["upwork"] and jobs[0].budget_max == 400
    assert jobs[0].external_id == "1aaa111bbb222ccc"
    assert len(notes) == 1 and "💬 Yangi xabar" in notes[0] and "rooms/1" in notes[0]
    assert "<untrusted-" in gem.prompts[0][0] and gem.prompts[0][1]["purpose"] == "email_extract"
    # next poll starts after the last processed UID
    assert await conn.fetch_jobs() == []
    assert fetcher.calls == [None, 12]


async def test_gemini_failure_stops_and_retries_same_email(sessionmaker):
    fetcher = FakeFetcher([mail(20, "a"), mail(21, "b")])
    gem = FakeGemini({20: ALERT, 21: RuntimeError("503")})
    conn = EmailWatchConnector(fetcher, gem, sessionmaker, None)
    await conn.fetch_jobs()
    assert fetcher.calls[-1] is None
    gem.by_uid[21] = PROMO
    await conn.fetch_jobs()
    assert fetcher.calls[-1] == 20  # email 21 retried


async def test_email_jobs_enter_pipeline_as_upwork(settings, sessionmaker):
    svc, sender, _ = make(settings, sessionmaker, [])
    fetcher = FakeFetcher([mail(30, "job")])
    alert = ALERT.model_copy(deep=True)
    svc.connectors = {"email": EmailWatchConnector(fetcher, FakeGemini({30: alert}), sessionmaker, None)}
    now = datetime(2026, 11, 17, 6, tzinfo=UTC)
    await svc.run_cycle(now)
    async with sessionmaker() as s:
        job = await s.scalar(select(Job).where(Job.platform == "upwork"))
    assert job is not None and job.status == JobStatus.AWAITING_APPROVAL
    text, _ = sender.messages[0]
    assert "Upwork (email)" in text
    assert await svc.approve(job.id, settings.owner_chat_id) == "manual"
    assert isinstance(svc, FreelanceService) and now - timedelta(hours=1) < now
