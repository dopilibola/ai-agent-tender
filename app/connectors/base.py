"""Freelance platform connector interface. Read-only parts first; submit lives behind approvals."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class JobListing(BaseModel):
    platform: str
    external_id: str
    url: str
    title: str
    description: str = ""
    budget_min: float | None = None
    budget_max: float | None = None
    currency: str = "USD"
    usd_rate: float | None = None  # 1 unit of currency in USD
    budget_type: str = "fixed"  # fixed | hourly
    proposals_count: int | None = None
    posted_at: datetime | None = None
    language: str | None = None
    skills: list[str] = []
    client_country: str | None = None
    client_rating: float | None = None
    client_total_spent_usd: float | None = None
    client_payment_verified: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def budget_usd(self) -> float | None:
        amount = self.budget_max or self.budget_min
        if amount is None or self.usd_rate is None:
            return None
        return round(amount * self.usd_rate, 2)


class Connector(Protocol):
    name: str
    title: str
    can_submit: bool  # False → approved proposals are handed to me to paste manually

    async def fetch_jobs(self) -> list[JobListing]: ...

    async def aclose(self) -> None: ...
