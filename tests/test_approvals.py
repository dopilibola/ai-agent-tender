import pytest

from app.core.approvals import (
    ApprovalRequired,
    NotOwner,
    consume_approval,
    decide,
    request_approval,
)
from app.db.models import ApprovalKind, ApprovalStatus

OWNER = 42
K = ApprovalKind.SUBMIT_PROPOSAL


async def test_cannot_act_without_approval(sessionmaker):
    async with sessionmaker() as s:
        await request_approval(s, kind=K, target_type="proposal", target_id=1)
        with pytest.raises(ApprovalRequired):
            await consume_approval(s, kind=K, target_type="proposal", target_id=1)


async def test_approve_then_consume_once(sessionmaker):
    async with sessionmaker() as s:
        a = await request_approval(s, kind=K, target_type="proposal", target_id=1)
        await decide(s, a.id, approved=True, user_id=OWNER, owner_id=OWNER)
        used = await consume_approval(s, kind=K, target_type="proposal", target_id=1)
        assert used.id == a.id and used.consumed_at is not None
        with pytest.raises(ApprovalRequired):  # a double-click can't submit twice
            await consume_approval(s, kind=K, target_type="proposal", target_id=1)


async def test_approval_is_target_and_kind_specific(sessionmaker):
    async with sessionmaker() as s:
        a = await request_approval(s, kind=K, target_type="proposal", target_id=1)
        await decide(s, a.id, approved=True, user_id=OWNER, owner_id=OWNER)
        with pytest.raises(ApprovalRequired):
            await consume_approval(s, kind=K, target_type="proposal", target_id=2)
        with pytest.raises(ApprovalRequired):
            await consume_approval(
                s, kind=ApprovalKind.DELIVER_WORK, target_type="proposal", target_id=1
            )


async def test_rejected_and_non_owner(sessionmaker):
    async with sessionmaker() as s:
        a = await request_approval(s, kind=K, target_type="proposal", target_id=1)
        with pytest.raises(NotOwner):
            await decide(s, a.id, approved=True, user_id=999, owner_id=OWNER)
        a = await decide(s, a.id, approved=False, user_id=OWNER, owner_id=OWNER)
        assert a.status == ApprovalStatus.REJECTED
        with pytest.raises(ApprovalRequired):  # can't flip a decided approval
            await decide(s, a.id, approved=True, user_id=OWNER, owner_id=OWNER)
        with pytest.raises(ApprovalRequired):
            await consume_approval(s, kind=K, target_type="proposal", target_id=1)


async def test_pending_is_reused(sessionmaker):
    async with sessionmaker() as s:
        a = await request_approval(s, kind=K, target_type="proposal", target_id=7)
        b = await request_approval(s, kind=K, target_type="proposal", target_id=7, payload={"v": 2})
        assert a.id == b.id and b.payload == {"v": 2}
