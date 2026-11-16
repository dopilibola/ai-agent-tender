"""Interface every procurement source implements (etender, xarid, xt-xarid, ...)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.tenders.schemas import LotFile, LotRecord, LotSummary


class TenderSource(Protocol):
    name: str
    title: str  # human name for messages, e.g. "etender.uzex.uz"

    async def list_open(self) -> list[LotSummary]:
        """All currently open lots (paged internally)."""

    async def get_lot(self, external_id: str) -> LotRecord:
        """Full lot card."""

    async def download(self, file: LotFile, dest: Path, *, max_bytes: int) -> int:
        """Save one attachment to `dest`; return its size."""

    async def aclose(self) -> None: ...
