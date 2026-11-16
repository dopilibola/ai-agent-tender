"""Persist every Gemini call (tokens, cost, latency) to the llm_calls table."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import LLMCall
from app.llm.gemini import LLMUsage, UsageSink


def make_db_sink(sessionmaker: async_sessionmaker) -> UsageSink:
    async def sink(u: LLMUsage) -> None:
        async with sessionmaker() as session:
            session.add(
                LLMCall(
                    model=u.model,
                    purpose=u.purpose,
                    prompt_tokens=u.prompt_tokens,
                    output_tokens=u.output_tokens,
                    cost_usd=u.cost_usd,
                    latency_ms=u.latency_ms,
                    ok=u.ok,
                    error=u.error,
                )
            )
            await session.commit()

    return sink


async def cost_since(sessionmaker: async_sessionmaker, hours: int = 24) -> tuple[int, float]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    async with sessionmaker() as session:
        stmt = select(
            func.count(LLMCall.id), func.coalesce(func.sum(LLMCall.cost_usd), 0.0)
        ).where(LLMCall.created_at >= since)
        row = (await session.execute(stmt)).one()
    return int(row[0]), float(row[1])
