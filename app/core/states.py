"""Job status state machine (pure, no DB) — the single source of allowed transitions."""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    FOUND = "found"
    SCORED = "scored"
    SKIPPED = "skipped"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    CLIENT_REPLIED = "client_replied"
    INTERVIEW = "interview"
    HIRED = "hired"
    IN_PROGRESS = "in_progress"
    REVIEW_READY = "review_ready"
    DELIVERED = "delivered"
    PAID = "paid"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ERROR = "error"


S = JobStatus

# Happy-path (and a few legitimate side) transitions.
_FORWARD: dict[JobStatus, set[JobStatus]] = {
    S.FOUND: {S.SCORED, S.SKIPPED},
    S.SCORED: {S.DRAFTED, S.SKIPPED},
    S.DRAFTED: {S.AWAITING_APPROVAL, S.SKIPPED},
    # Edit / rewrite goes back to DRAFTED; "skip" button -> SKIPPED.
    S.AWAITING_APPROVAL: {S.APPROVED, S.DRAFTED, S.SKIPPED},
    S.APPROVED: {S.SUBMITTED, S.AWAITING_APPROVAL},
    S.SUBMITTED: {S.CLIENT_REPLIED, S.INTERVIEW, S.HIRED},
    S.CLIENT_REPLIED: {S.INTERVIEW, S.HIRED},
    S.INTERVIEW: {S.HIRED},
    S.HIRED: {S.IN_PROGRESS},
    S.IN_PROGRESS: {S.REVIEW_READY},
    S.REVIEW_READY: {S.DELIVERED, S.IN_PROGRESS},  # rework after my review
    S.DELIVERED: {S.PAID, S.IN_PROGRESS},  # client asked for changes
    S.PAID: set(),
    S.SKIPPED: set(),
    S.REJECTED: set(),
    S.EXPIRED: set(),
    S.ERROR: set(),
}

TERMINAL: frozenset[JobStatus] = frozenset({S.PAID, S.SKIPPED, S.REJECTED, S.EXPIRED})
# Reachable from any non-terminal state.
_FROM_ANY: frozenset[JobStatus] = frozenset({S.REJECTED, S.EXPIRED, S.ERROR})


class InvalidTransition(ValueError):
    def __init__(self, current: JobStatus, target: JobStatus):
        super().__init__(f"Illegal job status transition: {current} -> {target}")
        self.current, self.target = current, target


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    current, target = JobStatus(current), JobStatus(target)
    if current == target or current in TERMINAL:
        return False
    if current == S.ERROR:
        # Manual recovery: from ERROR you may go anywhere except back to FOUND.
        return target != S.FOUND
    return target in _FROM_ANY or target in _FORWARD[current]


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(JobStatus(current), JobStatus(target))


# Uzbek labels for Telegram notifications.
STATUS_LABEL_UZ: dict[JobStatus, str] = {
    S.FOUND: "🔎 Topildi",
    S.SCORED: "📊 Baholandi",
    S.SKIPPED: "⏭ O'tkazib yuborildi",
    S.DRAFTED: "📝 Taklif yozildi",
    S.AWAITING_APPROVAL: "⏳ Tasdiq kutilmoqda",
    S.APPROVED: "✅ Tasdiqlandi",
    S.SUBMITTED: "📤 Yuborildi",
    S.CLIENT_REPLIED: "💬 Mijoz javob berdi",
    S.INTERVIEW: "🗣 Suhbat",
    S.HIRED: "🎉 Ishga olindingiz",
    S.IN_PROGRESS: "🛠 Bajarilmoqda",
    S.REVIEW_READY: "👀 Tekshiruvga tayyor",
    S.DELIVERED: "📦 Topshirildi",
    S.PAID: "💰 To'landi",
    S.REJECTED: "❌ Rad etildi",
    S.EXPIRED: "⌛ Muddati o'tdi",
    S.ERROR: "⚠️ Xatolik",
}
