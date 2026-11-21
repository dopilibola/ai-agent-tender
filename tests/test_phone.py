import contextlib
import json

from sqlalchemy import select

from app.core.approvals import decide, request_approval
from app.core.states import JobStatus
from app.db.models import Approval, ApprovalKind, Job, Proposal
from app.phone.agent import FOCUS_JS, BidAgent, BidFields, Step
from app.phone.device import PhoneUnavailable
from app.phone.submitter import PhoneSubmitter

PAGE = {"url": "https://www.freelancer.com/projects/x", "title": "x", "text": "Place a bid",
        "elements": [{"id": 1, "label": "Describe your proposal"}, {"id": 2, "label": "Bid amount"},
                     {"id": 3, "label": "Period"}, {"id": 4, "label": "Place Bid"}]}


class FakeTab:
    def __init__(self):
        self.values, self.focused, self.clicked, self.typed = {}, None, [], []

    async def eval(self, expr):
        if expr.startswith("(() =>"):
            return PAGE
        num = int(expr.rsplit("(", 1)[1].split(")")[0].split(",")[0])
        if expr == FOCUS_JS % num:
            self.focused = num
            return True
        if "e.click()" in expr:
            self.clicked.append(num)
            return True
        if "return e.isContentEditable ? e.innerText" in expr:
            return self.values.get(num)
        return None

    async def screenshot(self, fmt="jpeg", quality=45):
        return b"PNG"

    async def insert_text(self, text):
        self.typed.append(text)
        self.values[self.focused] = text

    async def wait_loaded(self, timeout=30):
        return None


class ScriptGemini:
    def __init__(self, steps):
        self.steps = list(steps)
        self.prompts = []

    async def generate_json(self, prompt, schema, **kw):
        self.prompts.append(prompt)
        return self.steps.pop(0)


BID = BidFields(proposal="Salom!\nTelegram bot 3 kunda.", price="120", currency="USD", days="3")
FILLS = [Step(action="fill", element=1, field="proposal"), Step(action="fill", element=2, field="price"),
         Step(action="fill", element=3, field="days")]


async def test_dry_run_stops_before_the_final_click():
    tab = FakeTab()
    gem = ScriptGemini([Step(action="submit", element=4), *FILLS, Step(action="submit", element=4)])
    res = await BidAgent(gem, step_delay_s=0).run(tab, BID, live=False)
    assert res.status == "ready" and tab.clicked == []
    assert tab.typed == [BID.proposal, "120", "3"]  # typed by code, never by the model
    assert "rad etildi" in res.steps[0]  # submit before filling is refused
    state = json.loads(gem.prompts[-1][1].split("\n", 1)[1])
    assert state["bid"]["proposal_chars"] == len(BID.proposal)


async def test_live_submit_needs_confirmation():
    tab = FakeTab()
    gem = ScriptGemini([Step(action="submitted"), *FILLS, Step(action="submit", element=4),
                        Step(action="submitted", reason="Bid placed")])
    res = await BidAgent(gem, step_delay_s=0).run(tab, BID, live=True)
    assert res.status == "submitted" and tab.clicked == [4]
    assert "Submit hali bosilmagan" in res.steps[0]


async def test_need_human_stops():
    res = await BidAgent(ScriptGemini([Step(action="need_human", reason="captcha")]), step_delay_s=0).run(
        FakeTab(), BID, live=True)
    assert res.status == "need_human" and res.reason == "captcha"


class FakePhone:
    def __init__(self, offline=False):
        self.offline, self.opened = offline, []

    async def ensure(self):
        if self.offline:
            raise PhoneUnavailable("wifi debug o'chiq")

    @contextlib.asynccontextmanager
    async def open(self, url):
        self.opened.append(url)
        yield FakeTab()


class FakeAgent:
    def __init__(self, status="submitted"):
        self.status, self.calls = status, 0

    async def run(self, tab, bid, *, live):
        from app.phone.agent import AgentResult
        self.calls += 1
        return AgentResult(self.status, "ok", ["0: x"], b"PNG", "https://f/x")


class Notes:
    def __init__(self):
        self.photos, self.texts = [], []

    async def send_photo(self, photo, caption=""):
        self.photos.append(caption)

    async def send(self, text, reply_markup=None, **kw):
        self.texts.append(text)


async def _approved_job(s, settings):
    job = Job(platform="freelancer_com", external_id="f1", url="https://www.freelancer.com/projects/x",
              title="Telegram bot", status=JobStatus.AWAITING_APPROVAL, fit=90)
    s.add(job)
    await s.flush()
    p = Proposal(job_id=job.id, text="Salom", price=120, currency="USD", timeline_days=3)
    s.add(p)
    await s.flush()
    a = await request_approval(s, kind=ApprovalKind.SUBMIT_PROPOSAL, target_type="proposal", target_id=p.id,
                               job_id=job.id, payload={})
    await decide(s, a.id, approved=True, user_id=settings.owner_chat_id, owner_id=settings.owner_chat_id)
    job.status = JobStatus.APPROVED
    await s.commit()
    return job.id, p.id


async def test_live_submit_marks_job_and_consumes_approval(settings, sessionmaker):
    settings.config.phone.dry_run = False
    async with sessionmaker() as s:
        job_id, pid = await _approved_job(s, settings)
    notes, agent = Notes(), FakeAgent("submitted")
    sub = PhoneSubmitter(settings, sessionmaker, None, notes, phone=FakePhone(), agent=agent)
    await sub.run_queue()
    await sub.run_queue()  # nothing left → no second bid
    assert agent.calls == 1
    async with sessionmaker() as s:
        assert (await s.get(Job, job_id)).status == JobStatus.SUBMITTED
        p = await s.get(Proposal, pid)
        assert p.submitted_at is not None and p.submit_result["phone"]["status"] == "submitted"
        assert (await s.scalar(select(Approval))).consumed_at is not None
    assert "Telefon topshirdi" in notes.photos[0]


async def test_dry_run_keeps_job_and_approval(settings, sessionmaker):
    settings.config.phone.dry_run = True
    async with sessionmaker() as s:
        job_id, _ = await _approved_job(s, settings)
    notes = Notes()
    await PhoneSubmitter(settings, sessionmaker, None, notes, phone=FakePhone(), agent=FakeAgent("ready")).run_queue()
    async with sessionmaker() as s:
        assert (await s.get(Job, job_id)).status == JobStatus.APPROVED
        assert (await s.scalar(select(Approval))).consumed_at is None
    assert "dry-run" in notes.photos[0]


async def test_offline_phone_retries_and_warns_once(settings, sessionmaker):
    settings.config.phone.dry_run = False
    async with sessionmaker() as s:
        await _approved_job(s, settings)
    notes, agent, phone = Notes(), FakeAgent(), FakePhone(offline=True)
    sub = PhoneSubmitter(settings, sessionmaker, None, notes, phone=phone, agent=agent)
    await sub.run_queue()
    assert agent.calls == 0 and len(notes.texts) == 1 and "Simsiz" in notes.texts[0]
    phone.offline = False
    await sub.run_queue()
    assert agent.calls == 1 and len(notes.texts) == 1
    async with sessionmaker() as s:
        assert (await s.scalar(select(Approval))).consumed_at is not None  # consumed only once it really ran


async def test_phone_prefers_fixed_port_and_restores_it_after_reboot(monkeypatch):
    import asyncio

    from app.phone.device import LOCAL, Phone

    state = {"local": False, "wifi": True, "tcp": False}
    calls = []

    async def run(*args, timeout=30):
        a = list(args[1:])
        if a[:1] == ["-s"]:
            a = a[2:]
        calls.append(a)
        if a[0] == "devices":
            lines = (["127.0.0.1:5555\tdevice"] if state["local"] else []) + (
                ["192.168.1.5:40000\tdevice"] if state["wifi"] else [])
            return "List of devices attached\n" + "\n".join(lines) + "\n"
        if a[0] == "tcpip":
            state["tcp"] = True
        if a[0] == "connect" and a[1] == LOCAL:
            state["local"] = state["tcp"]
        if a[0] == "disconnect":
            state["wifi"] = False
        return ""

    async def no_sleep(*_):
        return None

    ph = Phone("adb")
    monkeypatch.setattr(ph, "_run", run)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await ph._connect()  # after a reboot: only Wireless debugging → switch adbd to the fixed port
    assert ph.serial == LOCAL and ["tcpip", "5555"] in calls
    calls.clear()
    await ph._connect()  # next time: fixed port is used directly
    assert ph.serial == LOCAL and not any(c[0] == "tcpip" for c in calls)
