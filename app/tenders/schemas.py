"""Source-independent lot shapes. Every source module converts its own JSON into these."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LotSummary(BaseModel):
    """One row of a source's open-lots listing (cheap, no detail request)."""

    source: str
    external_id: str
    url: str
    title: str
    deadline_at: datetime | None = None
    start_cost: float | None = None
    currency: str = "UZS"
    customer_name: str | None = None
    customer_tin: str | None = None
    customer_region: str | None = None
    lot_type: str | None = None
    title_is_category: bool = False  # listing shows only a category; the real name is in the card


class LotFile(BaseModel):
    kind: str  # tz | tech_doc | additional | contract_draft | expertise
    label: str
    name: str | None = None
    path: str
    ext: str | None = None
    size: float | None = None
    local: str | None = None  # path on disk once downloaded


class Criterion(BaseModel):
    label: str
    max_score: float | None = None
    required: bool | None = None
    description: str | None = None


class LotRecord(LotSummary):
    """Full lot card."""

    description: str = ""
    published_at: datetime | None = None
    pledge_percent: float | None = None
    pledge_amount: float | None = None
    advance_percent: float | None = None
    payment_days: int | None = None
    execution_days: int | None = None
    warranty: str | None = None
    valuation_method: str | None = None
    price_weight: float | None = None
    tech_weight: float | None = None
    pass_mark: float | None = None
    criteria: list[Criterion] = []
    qualification: list[str] = []
    languages: list[str] = []
    financing_source: str | None = None
    files: list[LotFile] = []
    platform_status: str | None = None
    is_open: bool = True
    is_prolonged: bool = False
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)
