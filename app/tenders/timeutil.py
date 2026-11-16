"""Time helpers. Sources publish naive Asia/Tashkent times; the DB stores UTC; messages show Tashkent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TASHKENT = ZoneInfo("Asia/Tashkent")


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; everything we store is UTC, so naive means UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_local(value: str | None) -> datetime | None:
    """'2026-11-17T08:46:30' (Tashkent, no offset) -> aware UTC datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TASHKENT)
    return dt.astimezone(UTC)


def to_local(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(TASHKENT)  # type: ignore[union-attr]


def fmt_local(dt: datetime | None, with_time: bool = True) -> str:
    if dt is None:
        return "noma'lum"
    return to_local(dt).strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def remaining_uz(deadline: datetime | None, now: datetime | None = None) -> str:
    if deadline is None:
        return "muddat noma'lum"
    seconds = int((as_utc(deadline) - (now or now_utc())).total_seconds())  # type: ignore[operator]
    if seconds <= 0:
        return "muddati o'tgan"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} kun {hours} soat qoldi"
    if hours:
        return f"{hours} soat {minutes} daqiqa qoldi"
    return f"{minutes} daqiqa qoldi"


def subtract_workdays(dt: datetime, workdays: int) -> datetime:
    """Go back N Mon–Fri days in Tashkent time (public holidays are not modelled)."""
    local = to_local(dt)
    left = workdays
    while left > 0:
        local -= timedelta(days=1)
        if local.weekday() < 5:
            left -= 1
    return local.astimezone(UTC)
