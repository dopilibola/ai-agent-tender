"""Human-in-the-loop guard.

Every outgoing action (submit proposal, message client, accept contract, deliver work)
must call `consume_approval()` immediately before acting. It raises unless the owner
approved that exact target in Telegram, and marks the approval as used so the same
click can never trigger the action twice.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Approval, ApprovalKind, ApprovalStatus, utcnow


class ApprovalRequired(PermissionError):
    """Raised when an outgoing action is attempted without a fresh owner approval."""


class NotOwner(PermissionError):
    pass


async def request_approval(
    session: AsyncSession,
    *,
    kind: ApprovalKind,
    target_type: str,
    target_id: int,
    job_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> Approval:
    """Create (or reuse) a PENDING approval for this target."""
    existing = await session.scalar(
        select(Approval).where(
            Approval.kind == kind,
            Approval.target_type == target_type,
            Approval.target_id == target_id,
            Approval.status == ApprovalStatus.PENDING,
        )
    )
    if existing:
        if payload is not None:
            existing.payload = payload
        return existing
    approval = Approval(
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        job_id=job_id,
        payload=payload or {},
    )
    session.add(approval)
    await session.flush()
    return approval


async def decide(
    session: AsyncSession, approval_id: int, *, approved: bool, user_id: int, owner_id: int
) -> Approval:
    """Record the owner's button click. Only the owner may decide; only PENDING can change."""
    if user_id != owner_id:
        raise NotOwner(f"user {user_id} is not the owner")
    approval = await session.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise LookupError(f"approval {approval_id} not found")
    if approval.status != ApprovalStatus.PENDING:
        raise ApprovalRequired(f"approval {approval_id} already {approval.status}")
    approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    approval.decided_at = utcnow()
    approval.decided_by = user_id
    await session.flush()
    return approval


async def consume_approval(
    session: AsyncSession, *, kind: ApprovalKind, target_type: str, target_id: int
) -> Approval:
    """Return the APPROVED, unused approval for this target and mark it used — or raise."""
    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.kind == kind,
            Approval.target_type == target_type,
            Approval.target_id == target_id,
            Approval.status == ApprovalStatus.APPROVED,
            Approval.consumed_at.is_(None),
        )
        .order_by(Approval.decided_at.desc())
        .with_for_update()
    )
    if approval is None:
        raise ApprovalRequired(f"No owner approval for {kind} on {target_type}#{target_id}")
    approval.consumed_at = utcnow()
    await session.flush()
    return approval
