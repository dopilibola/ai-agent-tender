"""Daily UZS→USD rate from the Central Bank of Uzbekistan, cached in the KV table."""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import KV
from app.tenders.http import USER_AGENT
from app.tenders.timeutil import as_utc, now_utc

log = logging.getLogger(__name__)

CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/"
KV_KEY = "fx:USD"
MAX_AGE = timedelta(hours=12)


def parse_cbu(rows: list[dict]) -> tuple[float, str]:
    for row in rows:
        if row.get("Ccy") == "USD":
            return float(row["Rate"]) / float(row.get("Nominal") or 1), row.get("Date", "")
    raise ValueError("USD not found in CBU response")


def parse_cbu_all(rows: list[dict]) -> dict[str, float]:
    """{currency code: UZS per 1 unit}."""
    out = {"UZS": 1.0}
    for row in rows:
        try:
            out[row["Ccy"]] = float(row["Rate"]) / float(row.get("Nominal") or 1)
        except (KeyError, ValueError, TypeError):
            continue
    return out


class UsdRate:
    def __init__(self, sessionmaker: async_sessionmaker, transport: httpx.AsyncBaseTransport | None = None):
        self.sessionmaker = sessionmaker
        self.transport = transport

    async def get(self) -> float | None:
        """Cached rate (refreshed at most every 12 h). Returns the last known rate if CBU is down."""
        async with self.sessionmaker() as session:
            row = await session.get(KV, KV_KEY)
            cached = row.value if row else None
            fetched = cached and cached.get("fetched_at")
            if fetched and now_utc() - as_utc(_iso(fetched)) < MAX_AGE:
                return cached["rate"]
            try:
                async with httpx.AsyncClient(
                    timeout=20, headers={"User-Agent": USER_AGENT}, transport=self.transport
                ) as client:
                    resp = await client.get(CBU_URL)
                    resp.raise_for_status()
                    rows = resp.json()
                    rate, date = parse_cbu(rows)
                    all_rates = parse_cbu_all(rows)
            except Exception as e:
                log.warning("fx.refresh_failed", extra={"error": str(e)[:200]})
                return cached["rate"] if cached else None
            value = {"rate": rate, "date": date, "all": all_rates, "fetched_at": now_utc().isoformat()}
            if row:
                row.value = value
            else:
                session.add(KV(key=KV_KEY, value=value))
            await session.commit()
            log.info("fx.refreshed", extra={"rate": rate, "date": date})
            return rate

    async def usd_per_unit(self, currency: str) -> float | None:
        """How many USD one unit of `currency` is worth (via CBU UZS cross rates)."""
        currency = (currency or "USD").upper()
        if currency == "USD":
            return 1.0
        usd_uzs = await self.get()
        if not usd_uzs:
            return None
        async with self.sessionmaker() as session:
            row = await session.get(KV, KV_KEY)
        rates = (row.value or {}).get("all", {}) if row else {}
        if currency not in rates and currency != "UZS":
            # cached before cross rates were stored → force a refresh once
            async with self.sessionmaker() as session:
                row = await session.get(KV, KV_KEY)
                if row:
                    row.value = {**row.value, "fetched_at": "2000-01-01T00:00:00+00:00"}
                    await session.commit()
            await self.get()
            async with self.sessionmaker() as session:
                row = await session.get(KV, KV_KEY)
            rates = (row.value or {}).get("all", {}) if row else {}
        uzs = 1.0 if currency == "UZS" else rates.get(currency)
        return uzs / usd_uzs if uzs else None


def _iso(s: str):
    from datetime import datetime

    return datetime.fromisoformat(s)
