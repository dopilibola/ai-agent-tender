"""SQLAlchemy 2 models. Enums are stored as plain strings (portable SQLite <-> Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.states import JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum(e: type[StrEnum], length: int = 32) -> Enum:
    return Enum(
        e,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda x: [m.value for m in x],
    )


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {datetime: DateTime(timezone=True), dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# ── Enums ───────────────────────────────────────────────────────────────────
class ApprovalKind(StrEnum):
    SUBMIT_PROPOSAL = "submit_proposal"
    SEND_MESSAGE = "send_message"
    ACCEPT_CONTRACT = "accept_contract"
    APPROVE_PLAN = "approve_plan"
    DELIVER_WORK = "deliver_work"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MessageDirection(StrEnum):
    INBOUND = "in"
    OUTBOUND = "out"


# ── Tables ──────────────────────────────────────────────────────────────────
class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_jobs_platform_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(8))

    budget_min: Mapped[float | None] = mapped_column(Float)
    budget_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    budget_usd: Mapped[float | None] = mapped_column(Float)
    budget_type: Mapped[str | None] = mapped_column(String(16))  # fixed | hourly
    proposals_count: Mapped[int | None] = mapped_column(Integer)
    posted_at: Mapped[datetime | None]
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)

    client_country: Mapped[str | None] = mapped_column(String(64))
    client_rating: Mapped[float | None] = mapped_column(Float)
    client_total_spent_usd: Mapped[float | None] = mapped_column(Float)
    client_payment_verified: Mapped[bool | None] = mapped_column(Boolean)

    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.FOUND, index=True)

    # Scoring
    fit: Mapped[int | None] = mapped_column(Integer)
    win_probability: Mapped[int | None] = mapped_column(Integer)
    effort_hours: Mapped[float | None] = mapped_column(Float)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary_uz: Mapped[str | None] = mapped_column(Text)
    recommended: Mapped[bool | None] = mapped_column(Boolean)

    # Bid (copied from the approved proposal)
    quote: Mapped[float | None] = mapped_column(Float)
    deposit: Mapped[float | None] = mapped_column(Float)
    timeline_days: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)

    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    proposals: Mapped[list[Proposal]] = relationship(back_populates="job", order_by="Proposal.version")
    events: Mapped[list[StatusEvent]] = relationship(back_populates="job", order_by="StatusEvent.id")


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    deposit: Mapped[float | None] = mapped_column(Float)
    timeline_days: Mapped[int | None] = mapped_column(Integer)
    milestones: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submitted_at: Mapped[datetime | None]
    submit_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="proposals")


class Approval(Base):
    """Every outgoing action needs one of these in APPROVED state. Enforced in app.core.approvals."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ApprovalKind] = mapped_column(_enum(ApprovalKind))
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))  # proposal | message | project ...
    target_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[int | None] = mapped_column(Integer)  # telegram user id
    consumed_at: Mapped[datetime | None]  # set when the approved action was executed


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[JobStatus | None] = mapped_column(_enum(JobStatus))
    to_status: Mapped[JobStatus] = mapped_column(_enum(JobStatus))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped[Job] = relationship(back_populates="events")


class Quota(TimestampMixin, Base):
    __tablename__ = "quotas"
    __table_args__ = (UniqueConstraint("platform", "period", name="uq_quotas_platform_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(16))  # "2026-11" monthly, or "balance"
    unit: Mapped[str] = mapped_column(String(16), default="proposals")  # proposals | connects
    limit: Mapped[int | None] = mapped_column(Integer)  # None = unlimited
    used: Mapped[int] = mapped_column(Integer, default=0)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    workspace_path: Mapped[str] = mapped_column(String(1024))
    plan_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    artifact_path: Mapped[str | None] = mapped_column(String(1024))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_messages_platform_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128))
    direction: Mapped[MessageDirection] = mapped_column(_enum(MessageDirection, 4))
    author: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    draft_reply: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[datetime | None]


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class KV(Base):
    """Small runtime flags (paused, last summary sent, ...)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ── 🏛 Tenders ──────────────────────────────────────────────────────────────
class LotStatus(StrEnum):
    NEW = "new"            # found, notification not yet sent
    NOTIFIED = "notified"  # card sent to Telegram
    IGNORED = "ignored"    # I pressed "Kerak emas"
    BIDDING = "bidding"    # I'm preparing a bid → reminders on
    CLOSED = "closed"      # deadline passed / cancelled / gone


class SeenLot(Base):
    """Every lot id seen in a source listing (cheap dedupe; only IT matches go to `lots`)."""

    __tablename__ = "seen_lots"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_seen_lots_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, default="")
    name_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    detail_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class Lot(TimestampMixin, Base):
    __tablename__ = "lots"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_lots_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(64))  # lot number shown to users
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    lot_type: Mapped[str | None] = mapped_column(String(128))

    customer_name: Mapped[str | None] = mapped_column(String(512))
    customer_tin: Mapped[str | None] = mapped_column(String(32))
    customer_region: Mapped[str | None] = mapped_column(String(128))

    start_cost: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="UZS")
    usd_rate: Mapped[float | None] = mapped_column(Float)  # UZS per 1 USD at detection time
    pledge_percent: Mapped[float | None] = mapped_column(Float)
    pledge_amount: Mapped[float | None] = mapped_column(Float)
    advance_percent: Mapped[float | None] = mapped_column(Float)
    payment_days: Mapped[int | None] = mapped_column(Integer)
    execution_days: Mapped[int | None] = mapped_column(Integer)
    warranty: Mapped[str | None] = mapped_column(String(64))

    published_at: Mapped[datetime | None]
    deadline_at: Mapped[datetime | None] = mapped_column(index=True)  # stored UTC

    valuation_method: Mapped[str | None] = mapped_column(String(128))
    price_weight: Mapped[float | None] = mapped_column(Float)
    tech_weight: Mapped[float | None] = mapped_column(Float)
    pass_mark: Mapped[float | None] = mapped_column(Float)
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    qualification: Mapped[list[str]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    financing_source: Mapped[str | None] = mapped_column(String(256))
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    platform_status: Mapped[str | None] = mapped_column(String(128))
    is_prolonged: Mapped[bool] = mapped_column(Boolean, default=False)

    matched_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # Gemini (step 3)
    verdict: Mapped[str | None] = mapped_column(String(16))

    status: Mapped[LotStatus] = mapped_column(_enum(LotStatus, 16), default=LotStatus.NEW, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    notified_at: Mapped[datetime | None]
    reminders_sent: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
