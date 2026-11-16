import pytest
from sqlalchemy import select

from app.core.states import InvalidTransition, JobStatus, assert_transition, can_transition
from app.core.status import flush_notifications, transition
from app.db.models import Job, StatusEvent

S = JobStatus

HAPPY_PATH = [
    S.FOUND, S.SCORED, S.DRAFTED, S.AWAITING_APPROVAL, S.APPROVED, S.SUBMITTED,
    S.CLIENT_REPLIED, S.INTERVIEW, S.HIRED, S.IN_PROGRESS, S.REVIEW_READY, S.DELIVERED, S.PAID,
]


def test_happy_path_is_allowed():
    for a, b in zip(HAPPY_PATH, HAPPY_PATH[1:], strict=False):
        assert can_transition(a, b), f"{a} -> {b}"


@pytest.mark.parametrize(
    "a,b",
    [
        (S.FOUND, S.SUBMITTED),            # can't submit unscored
        (S.DRAFTED, S.SUBMITTED),          # can't skip approval
        (S.AWAITING_APPROVAL, S.SUBMITTED),
        (S.SCORED, S.APPROVED),
        (S.HIRED, S.DELIVERED),            # can't deliver without review
        (S.IN_PROGRESS, S.DELIVERED),
        (S.PAID, S.ERROR),                 # terminal
        (S.SKIPPED, S.SCORED),
        (S.FOUND, S.FOUND),                # self
    ],
)
def test_illegal(a, b):
    assert not can_transition(a, b)
    with pytest.raises(InvalidTransition):
        assert_transition(a, b)


@pytest.mark.parametrize("state", [s for s in S if s not in {S.PAID, S.SKIPPED, S.REJECTED,
                                                             S.EXPIRED, S.ERROR}])
def test_reject_expire_error_from_any_live_state(state):
    for target in (S.REJECTED, S.EXPIRED, S.ERROR):
        assert can_transition(state, target)


def test_error_recovery():
    assert can_transition(S.ERROR, S.SCORED)
    assert not can_transition(S.ERROR, S.FOUND)


async def test_transition_logs_event_and_notifies(sessionmaker):
    async with sessionmaker() as s:
        job = Job(platform="fake", external_id="1", url="u", title="T <b>x</b>")
        s.add(job)
        await s.flush()
        await transition(s, job, S.SCORED)          # quiet status
        await transition(s, job, S.REJECTED, "client chose another")
        await s.commit()
        with pytest.raises(InvalidTransition):
            await transition(s, job, S.SCORED)  # REJECTED is terminal

    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    assert await flush_notifications(sessionmaker, send) == 1
    assert "Rad etildi" in sent[0] and "&lt;b&gt;" in sent[0] and "client chose another" in sent[0]
    # Second flush sends nothing (already notified).
    assert await flush_notifications(sessionmaker, send) == 0

    async with sessionmaker() as s:
        events = (await s.scalars(select(StatusEvent).order_by(StatusEvent.id))).all()
        assert [(e.from_status, e.to_status) for e in events] == [
            (S.FOUND, S.SCORED), (S.SCORED, S.REJECTED)
        ]


async def test_failed_notification_is_retried(sessionmaker):
    async with sessionmaker() as s:
        job = Job(platform="fake", external_id="2", url="u", title="T")
        s.add(job)
        await s.flush()
        await transition(s, job, S.ERROR, "boom")
        await s.commit()

    async def broken(_: str) -> None:
        raise RuntimeError("telegram down")

    assert await flush_notifications(sessionmaker, broken) == 0
    got: list[str] = []

    async def ok(t: str) -> None:
        got.append(t)

    assert await flush_notifications(sessionmaker, ok) == 1
